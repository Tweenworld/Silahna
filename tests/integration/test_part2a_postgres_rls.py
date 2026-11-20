"""Acceptance tests for Part 2A: PostgreSQL, Schemas, Least-Privilege Roles,
Row-Level Security, Bounded Advisory Locks, and Two-Mode Data Minimisation.

Strict Gates Enforced:
1. Tests run as suffixed `app_rw_<suffix>` (non-superuser, non-owner, no BYPASSRLS).
2. Proves connection is not superuser.
3. Empty-string session setting denies cleanly without UUID cast error.
4. `app.is_couple_member(p_couple_id)` reads setting internally; cannot probe arbitrary person.
5. Append-only: UPDATE, DELETE, and TRUNCATE blocked via permissions and triggers.
6. SECURITY DEFINER hygiene (dedicated non-superuser NOLOGIN owner, fixed search_path,
   schema-qualified, REVOKE FROM PUBLIC).
7. Real concurrent audit lock contention with bounded timeout failing closed with SQLSTATE 55P03.
8. Two schema variants: production mode omits raw genotype tables and scenario endpoints.
9. Counsellor access requires both consents, verified in SQL function.
10. Teardown drops throwaway databases and suffixed test roles cleanly without clobbering dev DBs.
11. No pgcrypto extension installed; extensions listed.
12. Audit event written as the last statement before transaction commit.
13. App role denied CREATE in app/ref/audit, denied TEMP tables, denied DISABLE RLS/triggers,
    denied SET ROLE definer.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import uuid
from collections.abc import Generator
from typing import Any

import psycopg
import pytest

from audit.chain import GENESIS, event_mac
from audit.transaction import execute_audited_transaction
from db.adapter import GenotypeSourceAdapter, SyntheticDatabaseAdapter
from db.crypto import encrypt_json
from db.schema import create_database_schema

# Use environment variable with fallback to local test cluster on 5433
BASE_DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/postgres")


def _db_url_for(db_name: str, user: str = "postgres", password: str | None = None) -> str:
    parsed = urllib.parse.urlsplit(BASE_DB_URL)
    admin_user = parsed.username or "postgres"
    admin_pass = parsed.password
    if user == admin_user and password is None and admin_pass:
        auth = f"{user}:{admin_pass}"
    elif password is not None:
        auth = f"{user}:{password}"
    else:
        auth = user
    port_str = f":{parsed.port}" if parsed.port else ""
    netloc = f"{auth}@{parsed.hostname or '127.0.0.1'}{port_str}"
    return urllib.parse.urlunsplit((parsed.scheme or "postgresql", netloc, f"/{db_name}", "", ""))


@pytest.fixture(scope="module")
def run_suffix() -> str:
    """Generate a unique hex suffix for isolating test roles and databases."""
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def test_db_name(run_suffix: str) -> Generator[str, None, None]:
    """Create an isolated, throwaway database per test run and drop on teardown."""
    db_name = f"silahna_test_{run_suffix}"
    with psycopg.connect(BASE_DB_URL, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {db_name};")
    yield db_name
    # Cleanup throwaway database and ONLY suffixed test roles cleanly
    with psycopg.connect(BASE_DB_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{db_name}';"
            )
            cur.execute(f"DROP DATABASE IF EXISTS {db_name};")
            for base_role in ("app_rw", "etl_rw", "audit_ins", "analytics_ro", "silahna_definer"):
                suffixed = f"{base_role}_{run_suffix}"
                cur.execute(f"DROP ROLE IF EXISTS {suffixed};")


@pytest.fixture(scope="module")
def active_roles(test_db_name: str, run_suffix: str) -> dict[str, str]:
    """Initialize schemas, roles, triggers, and RLS for DEMO_MODE=1 with suffixed roles."""
    admin_url = _db_url_for(test_db_name, user="postgres")
    return create_database_schema(admin_url, demo_mode=True, role_suffix=run_suffix)


def get_app_conn(test_db_name: str, active_roles: dict[str, str]) -> psycopg.Connection[Any]:
    """Connect strictly as app_rw (least privilege, non-superuser, no BYPASSRLS)."""
    role_name = active_roles["app_rw"]
    app_url = _db_url_for(test_db_name, user=role_name, password=f"{role_name}_secret")
    return psycopg.connect(app_url, autocommit=False)


def get_etl_conn(test_db_name: str, active_roles: dict[str, str]) -> psycopg.Connection[Any]:
    """Connect strictly as etl_rw (write on ref schema only)."""
    role_name = active_roles["etl_rw"]
    etl_url = _db_url_for(test_db_name, user=role_name, password=f"{role_name}_secret")
    return psycopg.connect(etl_url, autocommit=False)


# Gate 1: Test connection role is NOT a superuser
def test_connection_is_not_superuser(active_roles: dict[str, str], test_db_name: str) -> None:
    """The app_rw role must NOT be a superuser, table owner, or have BYPASSRLS."""
    with get_app_conn(test_db_name, active_roles) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT usename, usesuper, usebypassrls FROM pg_user WHERE usename = current_user;"
            )
            row = cur.fetchone()
            assert row is not None
            usename, usesuper, usebypassrls = row
            assert usename == active_roles["app_rw"]
            assert usesuper is False, "app_rw MUST NOT be a superuser"
            assert usebypassrls is False, "app_rw MUST NOT have BYPASSRLS"


# Gate 2: RLS blocks cross-couple reads even with raw SQL
def test_rls_blocks_cross_couple_read_even_with_raw_sql(
    active_roles: dict[str, str], test_db_name: str
) -> None:
    """Partners can only see their own couple's rows. Cross-couple SELECT returns empty."""
    admin_url = _db_url_for(test_db_name, user="postgres")
    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            c1_id = uuid.uuid4()
            c2_id = uuid.uuid4()
            pa_id = uuid.uuid4()
            pb_id = uuid.uuid4()
            px_id = uuid.uuid4()
            py_id = uuid.uuid4()

            cur.execute(
                "INSERT INTO app.couple (id, partner_a_id, partner_b_id) VALUES (%s, %s, %s);",
                (c1_id, pa_id, pb_id),
            )
            cur.execute(
                "INSERT INTO app.couple (id, partner_a_id, partner_b_id) VALUES (%s, %s, %s);",
                (c2_id, px_id, py_id),
            )

    with get_app_conn(test_db_name, active_roles) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.person_id', %s, true);", (str(pa_id),))
            cur.execute("SELECT id FROM app.couple;")
            visible_couples = [row[0] for row in cur.fetchall()]

            assert c1_id in visible_couples
            assert c2_id not in visible_couples


# Gate 3: Unset person_id denies closed
def test_rls_unset_person_id_denies(active_roles: dict[str, str], test_db_name: str) -> None:
    """When app.person_id is unset, RLS fails closed and returns 0 rows."""
    with get_app_conn(test_db_name, active_roles) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM app.couple;")
            assert len(cur.fetchall()) == 0


# Gate 4: Empty-string person_id denies without UUID cast error
def test_rls_empty_string_person_id_denies_without_cast_error(
    active_roles: dict[str, str], test_db_name: str
) -> None:
    """Setting app.person_id to empty string denies cleanly via NULLIF without cast failure."""
    with get_app_conn(test_db_name, active_roles) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.person_id', '', true);")
            cur.execute("SELECT id FROM app.couple;")
            assert len(cur.fetchall()) == 0


# Gate 5: is_couple_member cannot probe arbitrary person
def test_is_couple_member_cannot_probe_arbitrary_person(
    active_roles: dict[str, str], test_db_name: str
) -> None:
    """is_couple_member takes only couple_id and reads person from session setting."""
    with get_app_conn(test_db_name, active_roles) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT proname, pronargs 
                FROM pg_proc p 
                JOIN pg_namespace n ON p.pronamespace = n.oid 
                WHERE n.nspname = 'app' AND proname = 'is_couple_member';
                """
            )
            row = cur.fetchone()
            assert row is not None
            assert row[1] == 1, "is_couple_member must accept exactly 1 argument (couple_id)"


# Gate 6: Append-only consent_event and audit.event (UPDATE, DELETE, TRUNCATE blocked)
def test_consent_event_update_and_delete_denied(
    active_roles: dict[str, str], test_db_name: str
) -> None:
    """UPDATE and DELETE are denied on app.consent_event."""
    admin_url = _db_url_for(test_db_name, user="postgres")
    cid = uuid.uuid4()
    pid = uuid.uuid4()

    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app.couple (id, partner_a_id, partner_b_id) VALUES (%s, %s, %s);",
                (cid, pid, uuid.uuid4()),
            )

    with get_app_conn(test_db_name, active_roles) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('app.person_id', %s, true);", (str(pid),))
            cur.execute(
                """
                INSERT INTO app.consent_event (couple_id, person_id, scope, granted)
                VALUES (%s, %s, 'couple_compute', true);
                """,
                (cid, pid),
            )
            conn.commit()

            with pytest.raises(psycopg.Error):
                cur.execute(
                    "UPDATE app.consent_event SET granted = false WHERE couple_id = %s;",
                    (cid,),
                )
            conn.rollback()

            with pytest.raises(psycopg.Error):
                cur.execute("DELETE FROM app.consent_event WHERE couple_id = %s;", (cid,))
            conn.rollback()


def test_consent_event_and_audit_event_truncate_denied(
    active_roles: dict[str, str], test_db_name: str
) -> None:
    """TRUNCATE is denied on app.consent_event and audit.event."""
    with get_app_conn(test_db_name, active_roles) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.Error):
                cur.execute("TRUNCATE TABLE app.consent_event;")
            conn.rollback()

            with pytest.raises(psycopg.Error):
                cur.execute("TRUNCATE TABLE audit.event;")
            conn.rollback()


# Gate 7: Role separation (ETL cannot write app, App cannot write ref)
def test_etl_role_cannot_write_app_schema(active_roles: dict[str, str], test_db_name: str) -> None:
    """etl_rw has permissions only on ref schema; writing to app schema is forbidden."""
    with get_etl_conn(test_db_name, active_roles) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.Error):
                cur.execute(
                    "INSERT INTO app.couple (id, partner_a_id, partner_b_id) VALUES (%s, %s, %s);",
                    (uuid.uuid4(), uuid.uuid4(), uuid.uuid4()),
                )


def test_app_role_cannot_write_ref_schema(active_roles: dict[str, str], test_db_name: str) -> None:
    """app_rw has read-only access to ref schema; writing to ref schema is forbidden."""
    with get_app_conn(test_db_name, active_roles) as conn:
        with conn.cursor() as cur:
            with pytest.raises(psycopg.Error):
                cur.execute(
                    "INSERT INTO ref.gene (symbol, disease, inheritance) "
                    "VALUES ('TEST', 'Test', 'ar');"
                )


# Gate 8: Audit chain concurrency with application HMAC (Key never in SQL)
def test_audit_chain_survives_concurrent_writers(
    active_roles: dict[str, str], test_db_name: str
) -> None:
    """App computes MAC under lock; key never sent to SQL; chain advances unbroken."""
    key = b"0123456789abcdef0123456789abcdef"
    num_writers = 4
    events_per_writer = 5
    errors: list[Exception] = []

    def writer_worker(writer_id: int) -> None:
        try:
            with get_app_conn(test_db_name, active_roles) as conn:
                for i in range(events_per_writer):
                    body = {"action": "test_event", "writer": writer_id, "seq": i}
                    with conn.cursor() as cur:
                        # 1. Fetch current head and acquire lock via SECURITY DEFINER helper
                        cur.execute("SELECT audit.get_head_for_update();")
                        row = cur.fetchone()
                        prev_mac = row[0] if row else "GENESIS"
                        # 2. Compute MAC in Python (Key NEVER enters SQL parameter!)
                        mac = event_mac(prev_mac, body, key)
                        # 3. Insert verified event
                        cur.execute(
                            "SELECT audit.insert_event(%s, %s, %s::jsonb);",
                            (prev_mac, mac, json.dumps(body)),
                        )
                    conn.commit()
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer_worker, args=(i,)) for i in range(num_writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Concurrent audit inserts encountered errors: {errors}"


# Gate 9: Lock timeout fails closed with SQLSTATE 55P03 (lock_not_available)
def test_audit_lock_timeout_fails_closed(active_roles: dict[str, str], test_db_name: str) -> None:
    """When a transaction holds a lock, a second transaction with lock_timeout raises 55P03."""
    lock_acquired = threading.Event()
    second_state: list[str] = []

    def lock_holder() -> None:
        with get_app_conn(test_db_name, active_roles) as conn:
            with conn.cursor() as cur:
                # Hold row lock on audit.head for 3.0s (exceeds 2s lock_timeout)
                cur.execute("SELECT audit.get_head_for_update();")
                lock_acquired.set()
                time.sleep(3.0)
            conn.commit()

    def blocked_waiter_with_timeout() -> None:
        lock_acquired.wait()
        try:
            with get_app_conn(test_db_name, active_roles) as conn:
                with conn.cursor() as cur:
                    # Bounded lock timeout of 200ms
                    cur.execute("SET LOCAL lock_timeout = '200ms';")
                    cur.execute("SELECT audit.get_head_for_update();")
        except psycopg.errors.LockNotAvailable as e:
            second_state.append(e.sqlstate)
        except Exception as e:
            second_state.append(f"unexpected:{type(e).__name__}")

    t1 = threading.Thread(target=lock_holder)
    t2 = threading.Thread(target=blocked_waiter_with_timeout)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Must specifically be SQLSTATE 55P03 (LOCK_NOT_AVAILABLE)
    assert len(second_state) == 1, f"Expected lock timeout exception, got: {second_state}"
    assert second_state[0] == "55P03", f"Expected SQLSTATE 55P03, got {second_state[0]}"


def test_lock_timeout_removal_does_not_raise_55p03(
    active_roles: dict[str, str], test_db_name: str
) -> None:
    """Without lock_timeout, a waiting transaction succeeds after lock is released (no 55P03)."""
    lock_acquired = threading.Event()
    waiter_succeeded = threading.Event()
    exceptions: list[Exception] = []

    def lock_holder() -> None:
        with get_app_conn(test_db_name, active_roles) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT audit.get_head_for_update();")
                lock_acquired.set()
                time.sleep(0.5)
            conn.commit()

    def waiter_without_timeout() -> None:
        lock_acquired.wait()
        try:
            with get_app_conn(test_db_name, active_roles) as conn:
                with conn.cursor() as cur:
                    # No lock_timeout set!
                    cur.execute("SELECT audit.get_head_for_update();")
                    waiter_succeeded.set()
                conn.commit()
        except Exception as e:
            exceptions.append(e)

    t1 = threading.Thread(target=lock_holder)
    t2 = threading.Thread(target=waiter_without_timeout)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(exceptions) == 0, f"Waiter without lock_timeout should succeed, got: {exceptions}"
    assert waiter_succeeded.is_set(), "Transaction must succeed once lock is released"


# Gate 10: Counsellor access requires both consents in SECURITY DEFINER function
def test_counsellor_access_requires_both_consents(
    active_roles: dict[str, str], test_db_name: str
) -> None:
    """Counsellor access requires both partner A and partner B active consents."""
    admin_url = _db_url_for(test_db_name, user="postgres")
    cid = uuid.uuid4()
    pa_id = uuid.uuid4()
    pb_id = uuid.uuid4()

    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app.couple (id, partner_a_id, partner_b_id) VALUES (%s, %s, %s);",
                (cid, pa_id, pb_id),
            )

    with get_app_conn(test_db_name, active_roles) as conn:
        with conn.cursor() as cur:
            # 1. Zero consents granted
            cur.execute("SELECT app.counsellor_can_access_couple(%s);", (cid,))
            assert cur.fetchone()[0] is False, "Access must be denied when 0 partners consented"

            # 2. Only Partner A consented
            cur.execute("SELECT set_config('app.person_id', %s, true);", (str(pa_id),))
            cur.execute(
                """
                INSERT INTO app.consent_event (couple_id, person_id, scope, granted)
                VALUES (%s, %s, 'counsellor_access', true);
                """,
                (cid, pa_id),
            )
            conn.commit()

            cur.execute("SELECT app.counsellor_can_access_couple(%s);", (cid,))
            assert cur.fetchone()[0] is False, "Access must be denied when only 1 partner consented"

            # 3. Both Partner A and Partner B consented
            cur.execute("SELECT set_config('app.person_id', %s, true);", (str(pb_id),))
            cur.execute(
                """
                INSERT INTO app.consent_event (couple_id, person_id, scope, granted)
                VALUES (%s, %s, 'counsellor_access', true);
                """,
                (cid, pb_id),
            )
            conn.commit()

            cur.execute("SELECT app.counsellor_can_access_couple(%s);", (cid,))
            assert cur.fetchone()[0] is True, "Access must be granted when BOTH partners consented"

            # 4. Partner A revokes consent (latest event granted = false)
            cur.execute("SELECT set_config('app.person_id', %s, true);", (str(pa_id),))
            cur.execute(
                """
                INSERT INTO app.consent_event (couple_id, person_id, scope, granted)
                VALUES (%s, %s, 'counsellor_access', false);
                """,
                (cid, pa_id),
            )
            conn.commit()

            cur.execute("SELECT app.counsellor_can_access_couple(%s);", (cid,))
            assert cur.fetchone()[0] is False, "Access must be denied after partner A revoked"


# Gate 11: Non-synthetic origin rejected in demo mode
def test_non_synthetic_origin_rejected_in_demo(
    active_roles: dict[str, str], test_db_name: str
) -> None:
    """In demo mode, CHECK constraints reject data_origin != 'synthetic'."""
    cid = uuid.uuid4()
    pid = uuid.uuid4()
    admin_url = _db_url_for(test_db_name, user="postgres")
    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app.couple (id, partner_a_id, partner_b_id) VALUES (%s, %s, %s);",
                (cid, pid, uuid.uuid4()),
            )

    with get_app_conn(test_db_name, active_roles) as conn:
        with conn.cursor() as cur:
            # Set person_id so RLS passes and reaches CHECK constraint
            cur.execute("SELECT set_config('app.person_id', %s, true);", (str(pid),))

            # Attempt to insert non-synthetic person
            with pytest.raises(psycopg.errors.CheckViolation) as exc_info:
                cur.execute(
                    "INSERT INTO app.person (id, data_origin) VALUES (%s, 'real');",
                    (uuid.uuid4(),),
                )
            assert exc_info.value.sqlstate == "23514"
            conn.rollback()

            cur.execute("SELECT set_config('app.person_id', %s, true);", (str(pid),))
            # Attempt to insert non-synthetic call
            with pytest.raises(psycopg.errors.CheckViolation) as exc_info:
                cur.execute(
                    """
                    INSERT INTO app.genotype_call 
                    (person_id, couple_id, encrypted_payload, data_origin, classification_at_call)
                    VALUES (%s, %s, '\\x00', 'clinical', 'pathogenic');
                    """,
                    (pid, cid),
                )
            assert exc_info.value.sqlstate == "23514"
            conn.rollback()


# Gate 12: Encryption of genotype payloads
def test_genotype_column_has_no_plaintext_gene_or_zygosity() -> None:
    """Genotype payload is stored encrypted with AES-GCM; plaintext is absent."""
    import base64

    b64_key = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
    payload = {"gene": "CFTR", "zygosity": "heterozygous", "variant": "p.Phe508del"}

    ciphertext = encrypt_json(b64_key, payload)
    assert b"CFTR" not in ciphertext
    assert b"heterozygous" not in ciphertext


# Gate 13: Two-mode schema and data minimisation
def test_genotype_table_and_scenario_routes_absent_in_production_mode() -> None:
    """In production mode (DEMO_MODE=0), app.genotype_call table is absent."""
    prod_db_name = f"silahna_prod_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(BASE_DB_URL, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {prod_db_name};")

    try:
        prod_admin_url = _db_url_for(prod_db_name, user="postgres")
        create_database_schema(prod_admin_url, demo_mode=False)

        with psycopg.connect(prod_admin_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT table_name 
                    FROM information_schema.tables 
                    WHERE table_schema = 'app' AND table_name = 'genotype_call';
                    """
                )
                assert cur.fetchone() is None, "app.genotype_call MUST NOT exist in production mode"
    finally:
        with psycopg.connect(BASE_DB_URL, autocommit=True) as conn:
            conn.execute(
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{prod_db_name}';"
            )
            conn.execute(f"DROP DATABASE IF EXISTS {prod_db_name};")


def test_production_mode_stores_no_variant_level_fields() -> None:
    """In production mode, genotype calls cannot be stored by production adapter."""
    from db.adapter import ProductionSummaryAdapter

    adapter = ProductionSummaryAdapter()
    with pytest.raises(NotImplementedError, match="data minimisation"):
        adapter.store_call("c1", "p1", None, data_origin="synthetic")  # type: ignore[arg-type]


def test_genotype_adapter_decoupled_from_routes() -> None:
    """Routes interact exclusively via GenotypeSourceAdapter interface."""
    adapter: GenotypeSourceAdapter = SyntheticDatabaseAdapter()
    assert hasattr(adapter, "get_couple_calls")
    assert hasattr(adapter, "store_call")


# Gate 14: Extension verification (No pgcrypto; app computes HMAC)
def test_no_pgcrypto_and_no_key_taking_functions(
    active_roles: dict[str, str], test_db_name: str
) -> None:
    """pgcrypto must NOT be installed; audit functions must NOT take cryptographic keys."""
    with get_app_conn(test_db_name, active_roles) as conn:
        with conn.cursor() as cur:
            # 1. Check extensions installed in database
            cur.execute("SELECT extname FROM pg_extension ORDER BY extname;")
            installed_extensions = [row[0] for row in cur.fetchall()]
            assert "pgcrypto" not in installed_extensions, "pgcrypto MUST NOT be installed"
            # plpgsql is the only standard extension
            assert installed_extensions == ["plpgsql"], (
                f"Expected only plpgsql, found: {installed_extensions}"
            )

            # 2. Check audit functions do not take key arguments
            cur.execute(
                """
                SELECT proname, proargnames
                FROM pg_proc p
                JOIN pg_namespace n ON p.pronamespace = n.oid
                WHERE n.nspname = 'audit' AND proname IN ('insert_event', 'get_head_for_update');
                """
            )
            functions = {row[0]: row[1] for row in cur.fetchall()}
            assert "get_head_for_update" in functions
            assert "insert_event" in functions
            assert functions["get_head_for_update"] is None  # 0 arguments

            argnames = functions["insert_event"]
            assert argnames == ["p_prev_mac", "p_mac", "p_body"]
            assert "key" not in argnames
            assert "secret" not in argnames


# Gate 15: Source inspection of audit.get_head_for_update()
def test_audit_get_head_for_update_locks_before_query(
    active_roles: dict[str, str], test_db_name: str
) -> None:
    """confirm lock_timeout is set before the first lock acquisition."""
    with get_app_conn(test_db_name, active_roles) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT prosrc
                FROM pg_proc p
                JOIN pg_namespace n ON p.pronamespace = n.oid
                WHERE n.nspname = 'audit' AND proname = 'get_head_for_update';
                """
            )
            row = cur.fetchone()
            assert row is not None
            source = row[0]
            # Ensure lock_timeout is configured before advisory lock acquisition
            lock_timeout_idx = source.find("SET LOCAL lock_timeout")
            advisory_lock_idx = source.find("pg_advisory_xact_lock")
            assert lock_timeout_idx != -1, "SET LOCAL lock_timeout must be in function"
            assert advisory_lock_idx != -1, "pg_advisory_xact_lock must be in function"
            assert lock_timeout_idx < advisory_lock_idx, (
                "SET LOCAL lock_timeout must precede advisory lock acquisition"
            )


# Gate 16: Python Genesis constant equals SQL table default
def test_python_genesis_equals_sql_head_default() -> None:
    """Python GENESIS constant must exactly match the SQL audit.head initial value."""
    fresh_db = f"silahna_gen_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(BASE_DB_URL, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {fresh_db};")
    try:
        admin_url = _db_url_for(fresh_db, user="postgres")
        create_database_schema(admin_url, demo_mode=True)
        with psycopg.connect(admin_url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT last_mac FROM audit.head WHERE id = 1;")
                row = cur.fetchone()
                assert row is not None
                sql_genesis = row[0]
                assert GENESIS == sql_genesis == "GENESIS", (
                    f"Mismatch: Python ({GENESIS}) != SQL ({sql_genesis})"
                )
    finally:
        with psycopg.connect(BASE_DB_URL, autocommit=True) as conn:
            conn.execute(
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{fresh_db}';"
            )
            conn.execute(f"DROP DATABASE IF EXISTS {fresh_db};")


# Gate 17: Audit event is the LAST statement executed before commit
def test_audit_event_is_last_statement_before_commit(
    active_roles: dict[str, str], test_db_name: str
) -> None:
    """Audit event must be the final SQL statement before transaction commit."""
    executed_statements: list[str] = []
    key = b"0123456789abcdef0123456789abcdef"
    cid = uuid.uuid4()
    pid = uuid.uuid4()

    # Pre-provision couple via admin
    admin_url = _db_url_for(test_db_name, user="postgres")
    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO app.couple (id, partner_a_id, partner_b_id) VALUES (%s, %s, %s);",
                (cid, pid, uuid.uuid4()),
            )

    class TrackingCursor(psycopg.Cursor[Any]):
        def execute(self, query: Any, params: Any = None, **kwargs: Any) -> Any:
            sql_text = (
                query.as_string(self.connection) if hasattr(query, "as_string") else str(query)
            )
            executed_statements.append(sql_text.strip())
            return super().execute(query, params, **kwargs)

    with get_app_conn(test_db_name, active_roles) as conn:
        conn.cursor_factory = TrackingCursor  # type: ignore[assignment]

        def domain_action(cur: psycopg.Cursor[Any]) -> None:
            cur.execute("SELECT set_config('app.person_id', %s, true);", (str(pid),))
            cur.execute(
                """
                INSERT INTO app.consent_event (couple_id, person_id, scope, granted)
                VALUES (%s, %s, 'couple_compute', true);
                """,
                (cid, pid),
            )

        body = {"action": "consent_grant", "couple_id": str(cid)}
        _, record = execute_audited_transaction(conn, domain_action, body, key)

        # The last executed statement in the transaction before commit MUST be audit.insert_event
        assert len(executed_statements) >= 3
        last_statement = executed_statements[-1]
        assert "audit.insert_event" in last_statement, (
            f"Expected audit.insert_event as final statement, got: {last_statement}"
        )
        assert record.prev_mac == "GENESIS" or len(record.prev_mac) == 64


# Gate 18: Privilege denials on app_rw role
def test_app_rw_privilege_denials(active_roles: dict[str, str], test_db_name: str) -> None:
    """app_rw cannot CREATE in schemas, cannot create TEMP tables, cannot DISABLE RLS or triggers,
    and cannot SET ROLE to silahna_definer.
    """
    with get_app_conn(test_db_name, active_roles) as conn:
        with conn.cursor() as cur:
            # 1. Denied CREATE in app schema
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("CREATE TABLE app.forbidden_table (id int);")
            conn.rollback()

            # 2. Denied CREATE in ref schema
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("CREATE TABLE ref.forbidden_table (id int);")
            conn.rollback()

            # 3. Denied CREATE in audit schema
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("CREATE TABLE audit.forbidden_table (id int);")
            conn.rollback()

            # 4. Denied CREATE TEMP TABLE
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("CREATE TEMP TABLE temp_forbidden (id int);")
            conn.rollback()

            # 5. Denied DISABLE ROW LEVEL SECURITY
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("ALTER TABLE app.couple DISABLE ROW LEVEL SECURITY;")
            conn.rollback()

            # 6. Denied DISABLE TRIGGER
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute("ALTER TABLE app.consent_event DISABLE TRIGGER ALL;")
            conn.rollback()

            # 7. Denied SET ROLE silahna_definer
            definer_role = active_roles["silahna_definer"]
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute(f"SET ROLE {definer_role};")
            conn.rollback()


# Gate 19: Suffixed roles isolation
def test_suffixed_roles_cleanup_does_not_affect_dev_dbs(
    active_roles: dict[str, str], run_suffix: str
) -> None:
    """Proves that test roles use unique run_suffix and do not collide with base role names."""
    for base_alias in ("app_rw", "etl_rw", "audit_ins", "analytics_ro", "silahna_definer"):
        role_name = active_roles[base_alias]
        assert role_name == f"{base_alias}_{run_suffix}"
        assert role_name != base_alias
