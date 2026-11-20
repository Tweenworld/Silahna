"""Transaction-safe audit writing.

Ensures the audit event is the last SQL statement executed before transaction commit.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import psycopg

from audit.chain import AuditRecord, event_mac


def execute_audited_transaction[T](
    conn: psycopg.Connection[Any],
    operation: Callable[[psycopg.Cursor[Any]], T],
    audit_body: dict[str, object],
    audit_key: bytes,
) -> tuple[T, AuditRecord]:
    """Execute domain operations and append an audit event as the final statement before commit.

    Sequence guaranteed:
    1. Execute domain operation.
    2. Lock audit head via SECURITY DEFINER helper.
    3. Compute HMAC in Python under the lock (key is never sent to SQL).
    4. Insert verified audit event as the LAST statement before commit.
    5. Commit immediately.
    """
    with conn.cursor() as cur:
        # 1. Execute domain operation
        result = operation(cur)

        # 2. Lock audit head
        cur.execute("SELECT audit.get_head_for_update();")
        row = cur.fetchone()
        prev_mac = row[0] if row else "GENESIS"

        # 3. Compute MAC in Python
        mac = event_mac(prev_mac, audit_body, audit_key)

        # 4. Insert audit record as the LAST statement
        cur.execute(
            "SELECT audit.insert_event(%s, %s, %s::jsonb);",
            (prev_mac, mac, json.dumps(audit_body)),
        )

    # 5. Commit immediately after audit event insertion
    conn.commit()
    return result, AuditRecord(prev_mac=prev_mac, mac=mac, body=audit_body)
