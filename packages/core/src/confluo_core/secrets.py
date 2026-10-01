"""Encrypted secrets per tenant (table `secret`, see migration 0009).

AES-256-GCM with the key from CONFLUO_SECRETS_KEY (32 bytes, base64). The secret's
id and tenant are bound into the ciphertext as associated data, so a ciphertext
copied to another row or tenant fails to decrypt.
"""

import base64
import json
import os
from typing import Any
from uuid import UUID, uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg import AsyncConnection

from confluo_core.settings import Settings


class SecretsNotConfigured(RuntimeError):
    pass


def _key(settings: Settings) -> bytes:
    if settings.secrets_key is None:
        raise SecretsNotConfigured("CONFLUO_SECRETS_KEY is not set")
    key = base64.urlsafe_b64decode(settings.secrets_key.get_secret_value())
    if len(key) != 32:
        raise SecretsNotConfigured("CONFLUO_SECRETS_KEY must be 32 bytes, base64-encoded")
    return key


def new_key() -> str:
    """A fresh value for CONFLUO_SECRETS_KEY."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode()


def seal(settings: Settings, data: bytes, *, aad: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(_key(settings)).encrypt(nonce, data, aad)


def unseal(settings: Settings, blob: bytes, *, aad: bytes) -> bytes:
    return AESGCM(_key(settings)).decrypt(blob[:12], blob[12:], aad)


async def store_secret(
    conn: AsyncConnection,
    settings: Settings,
    purpose: str,
    value: dict[str, Any],
    secret_id: UUID | None = None,
) -> UUID:
    """Create or replace a secret of the transaction's tenant; returns its id."""
    cur = await conn.execute("select app.current_tenant_id()")
    row = await cur.fetchone()
    tenant = row[0] if row else None
    if tenant is None:
        raise RuntimeError("store_secret needs a tenant context")
    sid = secret_id or uuid4()
    blob = seal(settings, json.dumps(value).encode(), aad=f"{tenant}:{sid}".encode())
    await conn.execute(
        "insert into secret (id, purpose, ciphertext) values (%s, %s, %s)"
        " on conflict (id) do update set ciphertext = excluded.ciphertext",
        (sid, purpose, blob),
    )
    return sid


async def load_secret(conn: AsyncConnection, settings: Settings, secret_id: UUID) -> dict[str, Any]:
    cur = await conn.execute("select tenant_id, ciphertext from secret where id = %s", (secret_id,))
    row = await cur.fetchone()
    if row is None:
        raise LookupError(str(secret_id))
    data = unseal(settings, bytes(row[1]), aad=f"{row[0]}:{secret_id}".encode())
    value: dict[str, Any] = json.loads(data)
    return value


async def delete_secret(conn: AsyncConnection, secret_id: UUID) -> None:
    await conn.execute("delete from secret where id = %s", (secret_id,))
