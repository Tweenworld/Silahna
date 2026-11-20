"""AES-GCM for genotype payloads. The key is not stored in the database."""

from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _key_bytes(key: str) -> bytes:
    try:
        raw = base64.b64decode(key, validate=True)
    except Exception as exc:
        raise ValueError("GENOTYPE_KEY must be base64") from exc
    if len(raw) != 32:
        raise ValueError("GENOTYPE_KEY must decode to 32 bytes")
    return raw


def encrypt_json(key: str, payload: dict[str, object]) -> bytes:
    nonce = os.urandom(12)
    token = AESGCM(_key_bytes(key)).encrypt(nonce, json.dumps(payload).encode(), None)
    return nonce + token


def decrypt_json(key: str, blob: bytes) -> dict[str, object]:
    nonce, token = blob[:12], blob[12:]
    raw = AESGCM(_key_bytes(key)).decrypt(nonce, token, None)
    loaded = json.loads(raw.decode())
    if not isinstance(loaded, dict):
        raise ValueError("genotype payload must be an object")
    return loaded
