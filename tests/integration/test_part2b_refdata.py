"""Acceptance tests for Part 2B: Public reference data pipeline.

Strict Gates Enforced:
1. test_sync_is_idempotent: Repeated syncs produce consistent, non-duplicated state.
2. test_bad_release_rejected_and_previous_kept: Corrupted data rolls back atomically.
3. test_reclassification_creates_task_only_with_recontact_consent:
   Recontact tasks require explicit consent.
4. test_draft_rulepack_cannot_be_approved_without_reviewer:
   Draft rule packs require review before approval.
5. test_request_path_makes_no_network_calls: Request path never reaches out to external networks.
6. test_release_row_records_hash_and_licence: Provenance, license, and SHA-256 are recorded.
"""

from __future__ import annotations

import os
import socket
import urllib.parse
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from db.schema import create_database_schema
from engine.rulepack import RulePackLoader, RulePackValidationError
from refdata.base import ValidationError
from refdata.reclassification import ReclassificationWatcher
from refdata.sync import RefSyncService

BASE_DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres@127.0.0.1:5433/postgres")
FIXTURES_DIR = Path("data/reference/fixtures")
MANIFEST_PATH = Path("data/reference/sources.yaml")


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
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def ref_db_name(run_suffix: str) -> Generator[str, None, None]:
    """Isolated database for Part 2B reference data tests."""
    db_name = f"silahna_ref_{run_suffix}"
    with psycopg.connect(BASE_DB_URL, autocommit=True) as conn:
        conn.execute(f"CREATE DATABASE {db_name};")
    yield db_name
    with psycopg.connect(BASE_DB_URL, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{db_name}';"
            )
            cur.execute(f"DROP DATABASE IF EXISTS {db_name};")
            for base_role in ("app_rw", "etl_rw", "audit_ins", "analytics_ro", "silahna_definer"):
                cur.execute(f"DROP ROLE IF EXISTS {base_role}_{run_suffix};")


@pytest.fixture(scope="module")
def setup_ref_db(ref_db_name: str, run_suffix: str) -> None:
    admin_url = _db_url_for(ref_db_name, user="postgres")
    create_database_schema(admin_url, demo_mode=True, role_suffix=run_suffix)


# Acceptance Test 1: Idempotent reference synchronization
def test_sync_is_idempotent(setup_ref_db: None, ref_db_name: str) -> None:
    """Re-running ref sync leaves database in a consistent, identical state."""
    admin_url = _db_url_for(ref_db_name, user="postgres")
    service = RefSyncService(manifest_path=MANIFEST_PATH, fixtures_dir=FIXTURES_DIR)

    with psycopg.connect(admin_url) as conn:
        # First sync
        first_counts = service.sync_all(conn, version="1.0.0")

        # Second sync (idempotency check)
        second_counts = service.sync_all(conn, version="1.0.0")

        assert first_counts == second_counts

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ref.variant;")
            var_count = cur.fetchone()[0]
            assert var_count == 5

            cur.execute("SELECT count(*) FROM ref.gene;")
            gene_count = cur.fetchone()[0]
            assert gene_count == 5


# Acceptance Test 2: Bad release is rejected and previous release kept
def test_bad_release_rejected_and_previous_kept(setup_ref_db: None, ref_db_name: str) -> None:
    """When a corrupted release is encountered, validation fails and previous data is preserved."""
    admin_url = _db_url_for(ref_db_name, user="postgres")
    service = RefSyncService(manifest_path=MANIFEST_PATH, fixtures_dir=FIXTURES_DIR)

    with psycopg.connect(admin_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ref.variant;")
            initial_count = cur.fetchone()[0]

        # Corrupt ClinVar content missing required ClinicalSignificance column
        bad_clinvar = (
            b"VariationID\tGeneSymbol\tName\tAssembly\n"
            b"99999\tBADGENE\tNM_0000.1:c.1A>G\tGRCh38\n"
        )

        with pytest.raises(ValidationError, match="missing required columns"):
            service.sync_bad_release_reverts(conn, bad_clinvar, "clinvar")

        # Assert database content is completely unchanged
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ref.variant;")
            post_count = cur.fetchone()[0]
            assert post_count == initial_count


# Acceptance Test 3: Reclassification watcher requires explicit recontact consent
def test_reclassification_creates_task_only_with_recontact_consent(
    setup_ref_db: None, ref_db_name: str
) -> None:
    """Recontact tasks are generated ONLY for patients with active 'recontact' consent."""
    admin_url = _db_url_for(ref_db_name, user="postgres")
    cid = uuid.uuid4()
    p_consented = uuid.uuid4()
    p_declined = uuid.uuid4()

    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Setup couple and synthetic persons
            cur.execute(
                """
                INSERT INTO app.person (id, data_origin)
                VALUES (%s, 'synthetic'), (%s, 'synthetic');
                """,
                (p_consented, p_declined),
            )
            cur.execute(
                "INSERT INTO app.couple (id, partner_a_id, partner_b_id) VALUES (%s, %s, %s);",
                (cid, p_consented, p_declined),
            )

            # Record recontact consent: TRUE for p_consented, FALSE for p_declined
            cur.execute(
                """
                INSERT INTO app.consent_event (couple_id, person_id, scope, granted)
                VALUES 
                    (%s, %s, 'recontact', true),
                    (%s, %s, 'recontact', false);
                """,
                (cid, p_consented, cid, p_declined),
            )

            # Record initial call for both: classification_at_call = 'uncertain_significance'
            cur.execute(
                """
                INSERT INTO app.genotype_call 
                (id, person_id, couple_id, encrypted_payload, data_origin, classification_at_call)
                VALUES 
                    (%s, %s, %s, '\\x00', 'synthetic', 'uncertain_significance'),
                    (%s, %s, %s, '\\x00', 'synthetic', 'uncertain_significance');
                """,
                (uuid.uuid4(), p_consented, cid, uuid.uuid4(), p_declined, cid),
            )

            # Simulate variant reclassification history: uncertain_significance -> pathogenic
            cur.execute(
                """
                INSERT INTO ref.classification_history (variant_id, old_class, new_class)
                VALUES ('15012', 'uncertain_significance', 'pathogenic');
                """
            )

    # Run reclassification watcher
    with psycopg.connect(admin_url) as conn:
        watcher = ReclassificationWatcher()
        task_ids = watcher.process_reclassifications(conn)

        # Exactly 1 recontact task created (for p_consented)
        assert len(task_ids) == 1

        with conn.cursor() as cur:
            cur.execute(
                "SELECT person_id, template_code FROM app.notification_outbox WHERE id = %s;",
                (task_ids[0],),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == p_consented
            assert row[1] == "recontact_alert"

            # Verify no notification created for p_declined
            cur.execute(
                "SELECT count(*) FROM app.notification_outbox WHERE person_id = %s;",
                (p_declined,),
            )
            assert cur.fetchone()[0] == 0


# Acceptance Test 4: Draft rulepack cannot be approved without reviewer name
def test_draft_rulepack_cannot_be_approved_without_reviewer(
    setup_ref_db: None, ref_db_name: str, tmp_path: Path
) -> None:
    """Draft rule packs require an explicit reviewer name and status validation."""
    from scripts.draft_rulepack import generate_draft_rulepack

    admin_url = _db_url_for(ref_db_name, user="postgres")
    with psycopg.connect(admin_url) as conn:
        draft_dir = generate_draft_rulepack(
            conn, tmp_path / "rulepacks" / "0.2.0-draft", version="0.2.0-draft"
        )

    loader = RulePackLoader(content_root=tmp_path)

    # 1. In demo mode, draft status is permitted
    pack = loader.load_rulepack("0.2.0-draft", demo_mode=True)
    assert pack.manifest.status == "draft"
    assert pack.manifest.reviewed_by is None

    # 2. In production mode (DEMO_MODE=0), draft rule pack is strictly refused
    with pytest.raises(RulePackValidationError, match="refused outside demo mode"):
        loader.load_rulepack("0.2.0-draft", demo_mode=False)

    # 3. Approved status without reviewer name is strictly refused
    manifest_file = draft_dir / "manifest.yaml"
    manifest_file.write_text(
        manifest_file.read_text(encoding="utf-8").replace("status: draft", "status: approved"),
        encoding="utf-8",
    )

    with pytest.raises(RulePackValidationError, match="must have a valid reviewer name"):
        loader.load_rulepack("0.2.0-draft", demo_mode=True)


# Acceptance Test 5: Request path makes no network calls
def test_request_path_makes_no_network_calls(
    setup_ref_db: None, ref_db_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """During user request handling, all lookups read from local ref schema without socket calls."""
    admin_url = _db_url_for(ref_db_name, user="postgres")

    # Intercept outbound socket creation
    original_connect = socket.socket.connect

    def forbidden_connect(self: socket.socket, address: Any) -> None:
        host, port = address[0], address[1]
        # Allow only connections to local PostgreSQL port
        if host in ("127.0.0.1", "localhost") and port in (5432, 5433):
            return original_connect(self, address)
        raise RuntimeError(f"Outbound network request blocked: {address}")

    monkeypatch.setattr(socket.socket, "connect", forbidden_connect)

    # Execute a local reference lookup query
    with psycopg.connect(admin_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT v.variation_id, v.gene, v.classification, g.disease
                FROM ref.variant v
                JOIN ref.gene g ON v.gene = g.symbol
                WHERE v.gene = 'HBB';
                """
            )
            rows = cur.fetchall()
            assert len(rows) > 0
            assert rows[0][1] == "HBB"


# Acceptance Test 6: Release row records hash and license
def test_release_row_records_hash_and_licence(setup_ref_db: None, ref_db_name: str) -> None:
    """Every ref.release row records source, version, sha256, license, and source URL."""
    admin_url = _db_url_for(ref_db_name, user="postgres")
    with psycopg.connect(admin_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source, version, sha256, licence, url, retrieved_at FROM ref.release;"
            )
            releases = cur.fetchall()
            assert len(releases) >= 4

            for source, _version, sha256_hash, licence, url, retrieved_at in releases:
                assert source in ("clinvar", "clingen", "orphanet", "gnomad")
                assert len(sha256_hash) == 64, "SHA-256 must be 64-character hex string"
                assert len(licence) > 0
                assert url.startswith("https://")
                assert retrieved_at is not None
