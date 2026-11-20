"""Reclassification watcher for notifying counsellors when variant classifications change."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg


class ReclassificationWatcher:
    """Watches for variant reclassifications and schedules recontact tasks.

    Crucial Privacy & Clinical Rule:
    A recontact task is created ONLY if the patient explicitly granted
    the 'recontact' consent scope (default is not granted).
    """

    def process_reclassifications(self, conn: psycopg.Connection[Any]) -> list[uuid.UUID]:
        """Scan for calls affected by reclassifications and create tasks where consented."""
        created_task_ids: list[uuid.UUID] = []

        with conn.cursor() as cur:
            # 1. Find all calls where classification_at_call differs from latest ref.variant
            # Note: in demo mode, app.genotype_call exists
            cur.execute(
                """
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'app' AND table_name = 'genotype_call';
                """
            )
            if not cur.fetchone():
                # In production mode without raw calls, reclassification watcher is a no-op
                return []

            cur.execute(
                """
                SELECT c.id, c.person_id, c.couple_id, c.classification_at_call, h.new_class
                FROM app.genotype_call c
                JOIN ref.classification_history h 
                  ON c.classification_at_call = h.old_class
                ORDER BY h.changed_at DESC;
                """
            )
            reclassified_calls = cur.fetchall()

            for _call_id, person_id, _couple_id, old_class, new_class in reclassified_calls:
                # 2. Check active recontact consent for this person
                cur.execute(
                    """
                    SELECT granted 
                    FROM app.consent_event 
                    WHERE person_id = %s AND scope = 'recontact'
                    ORDER BY recorded_at DESC, id DESC 
                    LIMIT 1;
                    """,
                    (person_id,),
                )
                row = cur.fetchone()
                has_recontact_consent = row[0] if row else False

                # 3. Create task ONLY if recontact consent is explicitly granted
                if has_recontact_consent:
                    task_id = uuid.uuid4()
                    msg = (
                        f"Recontact task: variant reclassified from {old_class} to {new_class} "
                        f"for person {person_id}."
                    )
                    cur.execute(
                        """
                        INSERT INTO app.notification_outbox (id, person_id, template_code, message)
                        VALUES (%s, %s, 'recontact_alert', %s);
                        """,
                        (task_id, person_id, msg),
                    )
                    created_task_ids.append(task_id)

        conn.commit()
        return created_task_ids
