"""PostgreSQL schemas, roles, row-level security, and audit functions (Part 2A)."""

from __future__ import annotations

import psycopg
from psycopg import sql


def create_database_schema(
    admin_url: str,
    *,
    demo_mode: bool = False,
    role_suffix: str = "",
) -> dict[str, str]:
    """Create schemas, roles, tables, functions, triggers, and RLS policies.

    Returns a dict mapping role aliases ('app_rw', 'etl_rw', 'audit_ins', 'analytics_ro',
    'silahna_definer') to their actual role names created in the database.
    """
    roles = {
        "app_rw": f"app_rw_{role_suffix}" if role_suffix else "app_rw",
        "etl_rw": f"etl_rw_{role_suffix}" if role_suffix else "etl_rw",
        "audit_ins": f"audit_ins_{role_suffix}" if role_suffix else "audit_ins",
        "analytics_ro": f"analytics_ro_{role_suffix}" if role_suffix else "analytics_ro",
        "silahna_definer": f"silahna_definer_{role_suffix}" if role_suffix else "silahna_definer",
    }
    definer_role = roles["silahna_definer"]

    with psycopg.connect(admin_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            # 1. Schemas (pgcrypto is NOT installed; app computes HMAC in Python)
            cur.execute("CREATE SCHEMA IF NOT EXISTS ref;")
            cur.execute("CREATE SCHEMA IF NOT EXISTS app;")
            cur.execute("CREATE SCHEMA IF NOT EXISTS audit;")

            # 2. Roles with least privilege
            for role_alias in ("app_rw", "etl_rw", "audit_ins", "analytics_ro"):
                role_name = roles[role_alias]
                password = f"{role_name}_secret"
                cur.execute(
                    sql.SQL(
                        """
                        DO $$
                        BEGIN
                            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {r_name}) THEN
                                EXECUTE format(
                                    'CREATE ROLE %I WITH LOGIN PASSWORD %L ' ||
                                    'NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE',
                                    {r_name}, {r_pass}
                                );
                            END IF;
                        END
                        $$;
                        """
                    ).format(r_name=sql.Literal(role_name), r_pass=sql.Literal(password))
                )

            # Dedicated non-superuser NOLOGIN role to own SECURITY DEFINER functions and tables
            cur.execute(
                sql.SQL(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {r_name}) THEN
                            EXECUTE format(
                                'CREATE ROLE %I WITH NOLOGIN ' ||
                                'NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE',
                                {r_name}
                            );
                        END IF;
                    END
                    $$;
                    """
                ).format(r_name=sql.Literal(definer_role))
            )

            # 3. Reference Data Tables (ref schema)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ref.release (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    source varchar NOT NULL,
                    version varchar NOT NULL,
                    retrieved_at timestamptz DEFAULT clock_timestamp(),
                    sha256 varchar NOT NULL,
                    licence varchar NOT NULL,
                    url varchar NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ref.gene (
                    symbol varchar PRIMARY KEY,
                    disease varchar NOT NULL,
                    inheritance varchar NOT NULL,
                    validity varchar,
                    minimum_review_status varchar
                );

                CREATE TABLE IF NOT EXISTS ref.variant (
                    variation_id varchar PRIMARY KEY,
                    gene varchar NOT NULL,
                    notation varchar NOT NULL,
                    classification varchar NOT NULL,
                    review_status varchar NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ref.classification_history (
                    id bigserial PRIMARY KEY,
                    variant_id varchar NOT NULL,
                    old_class varchar NOT NULL,
                    new_class varchar NOT NULL,
                    changed_at timestamptz DEFAULT clock_timestamp()
                );

                CREATE TABLE IF NOT EXISTS ref.disease (
                    id varchar PRIMARY KEY,
                    name varchar NOT NULL,
                    orpha_code varchar,
                    omim_id varchar
                );

                CREATE TABLE IF NOT EXISTS ref.allele_frequency (
                    variant_id varchar NOT NULL,
                    population varchar NOT NULL,
                    af numeric NOT NULL,
                    PRIMARY KEY (variant_id, population)
                );

                CREATE TABLE IF NOT EXISTS ref.rulepack (
                    version varchar PRIMARY KEY,
                    status varchar NOT NULL,
                    reviewed_by varchar,
                    reviewed_on date,
                    manifest jsonb NOT NULL,
                    panel jsonb NOT NULL
                );
                """
            )

            # 4. Application Tables (app schema)
            person_constraint = (
                "CHECK (data_origin = 'synthetic')"
                if demo_mode
                else "CHECK (data_origin IN ('synthetic', 'real'))"
            )
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS app.person (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    data_origin varchar NOT NULL {person_constraint}
                );

                CREATE TABLE IF NOT EXISTS app.couple (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    partner_a_id uuid NOT NULL,
                    partner_b_id uuid NOT NULL,
                    state varchar NOT NULL DEFAULT 'active'
                );

                CREATE TABLE IF NOT EXISTS app.pairing_code (
                    code_hash varchar PRIMARY KEY,
                    creator_id uuid NOT NULL,
                    created_at timestamptz DEFAULT clock_timestamp(),
                    expires_at timestamptz NOT NULL,
                    claimed_at timestamptz
                );

                CREATE TABLE IF NOT EXISTS app.consent_event (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    couple_id uuid NOT NULL,
                    person_id uuid NOT NULL,
                    scope varchar NOT NULL,
                    granted boolean NOT NULL,
                    recorded_at timestamptz DEFAULT clock_timestamp()
                );

                CREATE TABLE IF NOT EXISTS app.result_snapshot (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    couple_id uuid NOT NULL,
                    engine_version varchar NOT NULL,
                    ruleset_version varchar NOT NULL,
                    ref_release_id uuid,
                    encrypted_summary bytea NOT NULL,
                    created_at timestamptz DEFAULT clock_timestamp()
                );

                CREATE TABLE IF NOT EXISTS app.referral (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    couple_id uuid NOT NULL,
                    state varchar NOT NULL,
                    created_at timestamptz DEFAULT clock_timestamp()
                );

                CREATE TABLE IF NOT EXISTS app.referral_event (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    referral_id uuid NOT NULL,
                    event_type varchar NOT NULL,
                    actor_id uuid,
                    created_at timestamptz DEFAULT clock_timestamp()
                );

                CREATE TABLE IF NOT EXISTS app.notification_outbox (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    person_id uuid NOT NULL,
                    template_code varchar NOT NULL,
                    message text NOT NULL,
                    created_at timestamptz DEFAULT clock_timestamp()
                );

                CREATE TABLE IF NOT EXISTS app.teachback_response (
                    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                    couple_id uuid NOT NULL,
                    person_id uuid NOT NULL,
                    score int NOT NULL,
                    recorded_at timestamptz DEFAULT clock_timestamp()
                );
                """
            )

            # In DEMO_MODE=1, create genotype_call table (strictly synthetic)
            if demo_mode:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS app.genotype_call (
                        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                        person_id uuid NOT NULL,
                        couple_id uuid NOT NULL,
                        encrypted_payload bytea NOT NULL,
                        data_origin varchar NOT NULL CHECK (data_origin = 'synthetic'),
                        classification_at_call varchar NOT NULL,
                        recorded_at timestamptz DEFAULT clock_timestamp()
                    );
                    """
                )

            # 5. Audit Tables (audit schema)
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS audit.event (
                    id bigserial PRIMARY KEY,
                    prev_mac varchar NOT NULL,
                    mac varchar NOT NULL,
                    body jsonb NOT NULL,
                    recorded_at timestamptz DEFAULT clock_timestamp()
                );

                CREATE TABLE IF NOT EXISTS audit.head (
                    id int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
                    last_mac varchar NOT NULL,
                    updated_at timestamptz DEFAULT clock_timestamp()
                );

                INSERT INTO audit.head (id, last_mac)
                VALUES (1, 'GENESIS')
                ON CONFLICT (id) DO NOTHING;
                """
            )

            # 6. Append-Only Trigger Function & Denials
            cur.execute(
                sql.SQL(
                    """
                    CREATE OR REPLACE FUNCTION app.deny_mutation()
                    RETURNS trigger AS $$
                    BEGIN
                        RAISE EXCEPTION 'Append-only table: mutation is strictly forbidden';
                    END;
                    $$ LANGUAGE plpgsql;

                    ALTER FUNCTION app.deny_mutation() OWNER TO {definer};

                    DROP TRIGGER IF EXISTS deny_mutation_consent ON app.consent_event;
                    CREATE TRIGGER deny_mutation_consent
                    BEFORE UPDATE OR DELETE OR TRUNCATE ON app.consent_event
                    FOR EACH STATEMENT EXECUTE FUNCTION app.deny_mutation();

                    DROP TRIGGER IF EXISTS deny_mutation_audit ON audit.event;
                    CREATE TRIGGER deny_mutation_audit
                    BEFORE UPDATE OR DELETE OR TRUNCATE ON audit.event
                    FOR EACH STATEMENT EXECUTE FUNCTION app.deny_mutation();
                    """
                ).format(definer=sql.Identifier(definer_role))
            )

            # 7. SECURITY DEFINER Helpers: is_couple_member and counsellor_can_access_couple
            # Owned by dedicated non-superuser role silahna_definer (bypasses RLS on owned tables)
            cur.execute(
                sql.SQL(
                    """
                    CREATE OR REPLACE FUNCTION app.is_couple_member(p_couple_id uuid)
                    RETURNS boolean AS $$
                    DECLARE
                        v_person_id uuid;
                    BEGIN
                        v_person_id := NULLIF(current_setting('app.person_id', true), '')::uuid;
                        IF v_person_id IS NULL THEN
                            RETURN false;
                        END IF;
                        RETURN EXISTS (
                            SELECT 1 FROM app.couple c
                            WHERE c.id = p_couple_id
                              AND (c.partner_a_id = v_person_id OR c.partner_b_id = v_person_id)
                        );
                    END;
                    $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = app, pg_temp;

                    ALTER FUNCTION app.is_couple_member(uuid) OWNER TO {definer};
                    REVOKE ALL ON FUNCTION app.is_couple_member(uuid) FROM PUBLIC;
                    GRANT EXECUTE ON FUNCTION app.is_couple_member(uuid) TO {app_rw};

                    CREATE OR REPLACE FUNCTION app.counsellor_can_access_couple(p_couple_id uuid)
                    RETURNS boolean AS $$
                    DECLARE
                        v_c app.couple%ROWTYPE;
                        v_consent_a boolean;
                        v_consent_b boolean;
                    BEGIN
                        SELECT * INTO v_c FROM app.couple WHERE id = p_couple_id;
                        IF NOT FOUND THEN
                            RETURN false;
                        END IF;

                        -- Check active consent for partner A
                        SELECT granted INTO v_consent_a
                        FROM app.consent_event
                        WHERE couple_id = p_couple_id
                          AND person_id = v_c.partner_a_id
                          AND scope = 'counsellor_access'
                        ORDER BY recorded_at DESC, id DESC
                        LIMIT 1;

                        -- Check active consent for partner B
                        SELECT granted INTO v_consent_b
                        FROM app.consent_event
                        WHERE couple_id = p_couple_id
                          AND person_id = v_c.partner_b_id
                          AND scope = 'counsellor_access'
                        ORDER BY recorded_at DESC, id DESC
                        LIMIT 1;

                        RETURN (v_consent_a IS TRUE AND v_consent_b IS TRUE);
                    END;
                    $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = app, pg_temp;

                    ALTER FUNCTION app.counsellor_can_access_couple(uuid) OWNER TO {definer};
                    REVOKE ALL ON FUNCTION app.counsellor_can_access_couple(uuid) FROM PUBLIC;
                    GRANT EXECUTE ON FUNCTION app.counsellor_can_access_couple(uuid) TO {app_rw};
                    """
                ).format(
                    definer=sql.Identifier(definer_role),
                    app_rw=sql.Identifier(roles["app_rw"]),
                )
            )

            # 8. Row-Level Security Policies & Table Ownership
            cur.execute(
                sql.SQL(
                    """
                    ALTER TABLE app.couple OWNER TO {definer};
                    ALTER TABLE app.consent_event OWNER TO {definer};
                    ALTER TABLE audit.event OWNER TO {definer};
                    ALTER TABLE audit.head OWNER TO {definer};

                    GRANT USAGE ON SCHEMA app, audit TO {definer};
                    GRANT ALL ON ALL TABLES IN SCHEMA app, audit TO {definer};

                    ALTER TABLE app.couple ENABLE ROW LEVEL SECURITY;
                    DROP POLICY IF EXISTS couple_member_policy ON app.couple;
                    CREATE POLICY couple_member_policy ON app.couple
                    FOR ALL USING (app.is_couple_member(id));

                    ALTER TABLE app.consent_event ENABLE ROW LEVEL SECURITY;
                    DROP POLICY IF EXISTS consent_member_policy ON app.consent_event;
                    CREATE POLICY consent_member_policy ON app.consent_event
                    FOR ALL USING (app.is_couple_member(couple_id));
                    """
                ).format(definer=sql.Identifier(definer_role))
            )

            if demo_mode:
                cur.execute(
                    sql.SQL(
                        """
                        ALTER TABLE app.genotype_call OWNER TO {definer};
                        ALTER TABLE app.genotype_call ENABLE ROW LEVEL SECURITY;
                        DROP POLICY IF EXISTS genotype_member_policy ON app.genotype_call;
                        CREATE POLICY genotype_member_policy ON app.genotype_call
                        FOR ALL USING (app.is_couple_member(couple_id));
                        """
                    ).format(definer=sql.Identifier(definer_role))
                )

            # 9. SECURITY DEFINER Audit Chain Writer
            # Key is computed by application under lock; never passed as a SQL parameter!
            cur.execute(
                sql.SQL(
                    """
                    CREATE OR REPLACE FUNCTION audit.get_head_for_update()
                    RETURNS varchar AS $$
                    DECLARE
                        v_head varchar;
                    BEGIN
                        SET LOCAL lock_timeout = '2s';
                        PERFORM pg_advisory_xact_lock(hashtextextended('audit_chain', 0));
                        SELECT last_mac INTO v_head FROM audit.head WHERE id = 1 FOR UPDATE;
                        RETURN COALESCE(v_head, 'GENESIS');
                    END;
                    $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = audit, pg_temp;

                    ALTER FUNCTION audit.get_head_for_update() OWNER TO {definer};
                    REVOKE ALL ON FUNCTION audit.get_head_for_update() FROM PUBLIC;
                    GRANT EXECUTE ON FUNCTION audit.get_head_for_update() TO {app_rw}, {audit_ins};

                    CREATE OR REPLACE FUNCTION audit.insert_event(
                        p_prev_mac varchar,
                        p_mac varchar,
                        p_body jsonb
                    )
                    RETURNS varchar AS $$
                    DECLARE
                        v_last_mac varchar;
                    BEGIN
                        -- Bounded lock timeout (fails closed if lock cannot be acquired within 2s)
                        SET LOCAL lock_timeout = '2s';
                        PERFORM pg_advisory_xact_lock(hashtextextended('audit_chain', 0));

                        -- Retrieve and lock head
                        SELECT last_mac INTO v_last_mac FROM audit.head WHERE id = 1 FOR UPDATE;
                        IF v_last_mac IS NULL THEN
                            v_last_mac := 'GENESIS';
                        END IF;

                        -- Verify previous linkage matches chain head
                        IF v_last_mac != p_prev_mac THEN
                            RAISE EXCEPTION 'Audit chain linkage mismatch: expected %, got %',
                                v_last_mac, p_prev_mac;
                        END IF;

                        -- Insert verified record
                        INSERT INTO audit.event (prev_mac, mac, body)
                        VALUES (p_prev_mac, p_mac, p_body);

                        -- Advance head
                        UPDATE audit.head
                        SET last_mac = p_mac, updated_at = clock_timestamp()
                        WHERE id = 1;

                        RETURN p_mac;
                    END;
                    $$ LANGUAGE plpgsql SECURITY DEFINER SET search_path = audit, pg_temp;

                    ALTER FUNCTION audit.insert_event(varchar, varchar, jsonb)
                        OWNER TO {definer};
                    REVOKE ALL ON FUNCTION audit.insert_event(varchar, varchar, jsonb) FROM PUBLIC;
                    GRANT EXECUTE ON FUNCTION audit.insert_event(varchar, varchar, jsonb)
                        TO {app_rw}, {audit_ins};
                    """
                ).format(
                    definer=sql.Identifier(definer_role),
                    app_rw=sql.Identifier(roles["app_rw"]),
                    audit_ins=sql.Identifier(roles["audit_ins"]),
                )
            )

            # 10. Least-Privilege Permissions on Schemas and Tables
            cur.execute("SELECT current_database();")
            row = cur.fetchone()
            current_db = row[0] if row else "postgres"

            cur.execute(
                sql.SQL(
                    """
                    -- ref schema permissions
                    GRANT USAGE ON SCHEMA ref TO {app_rw}, {etl_rw}, {analytics_ro};
                    GRANT SELECT ON ALL TABLES IN SCHEMA ref TO {app_rw}, {analytics_ro};
                    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA ref TO {etl_rw};
                    REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES
                        IN SCHEMA ref FROM {app_rw};

                    -- app schema permissions
                    REVOKE ALL ON SCHEMA app FROM {etl_rw};
                    GRANT USAGE ON SCHEMA app TO {app_rw}, {analytics_ro};
                    GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA app TO {app_rw};
                    REVOKE UPDATE, DELETE, TRUNCATE ON app.consent_event FROM {app_rw};

                    -- audit schema permissions
                    GRANT USAGE ON SCHEMA audit TO {app_rw}, {audit_ins};
                    GRANT SELECT ON audit.event TO {app_rw};
                    REVOKE UPDATE, DELETE, TRUNCATE ON audit.event FROM {app_rw}, {audit_ins};

                    -- Strict security guardrails: deny CREATE in schemas and deny TEMP on database
                    REVOKE CREATE ON SCHEMA app, ref, audit FROM {app_rw}, PUBLIC;
                    REVOKE TEMPORARY ON DATABASE {db_name} FROM {app_rw}, PUBLIC;
                    """
                ).format(
                    app_rw=sql.Identifier(roles["app_rw"]),
                    etl_rw=sql.Identifier(roles["etl_rw"]),
                    audit_ins=sql.Identifier(roles["audit_ins"]),
                    analytics_ro=sql.Identifier(roles["analytics_ro"]),
                    db_name=sql.Identifier(current_db),
                )
            )

    return roles
