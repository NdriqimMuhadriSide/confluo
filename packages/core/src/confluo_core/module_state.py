"""Which modules a tenant has enabled, and their validated config."""

from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from confluo_core.modules import ConfluoModule


@dataclass(frozen=True)
class ModuleState:
    module: ConfluoModule
    enabled: bool
    config: BaseModel


class ModuleStateError(ValueError):
    pass


async def load_states(
    conn: AsyncConnection, modules: dict[str, ConfluoModule]
) -> dict[str, ModuleState]:
    """States of all installed modules for the transaction's tenant."""
    cur = await conn.execute(
        "select module_key, enabled, config from tenant_module"
        " where tenant_id = app.current_tenant_id()"
    )
    rows: dict[str, tuple[bool, dict[str, Any]]] = {
        r[0]: (r[1], r[2]) for r in await cur.fetchall()
    }
    states: dict[str, ModuleState] = {}
    for key, module in modules.items():
        enabled, raw = rows.get(key, (module.enabled_by_default, {}))
        # Stored config was validated on save; unknown keys from older versions are
        # dropped by the schema rather than failing the request.
        states[key] = ModuleState(module, enabled, module.config_schema.model_validate(raw))
    return states


async def is_enabled(conn: AsyncConnection, module: ConfluoModule) -> bool:
    cur = await conn.execute(
        "select enabled from tenant_module"
        " where tenant_id = app.current_tenant_id() and module_key = %s",
        (module.key,),
    )
    row = await cur.fetchone()
    return module.enabled_by_default if row is None else bool(row[0])


async def save_state(
    conn: AsyncConnection,
    modules: dict[str, ConfluoModule],
    key: str,
    *,
    enabled: bool | None = None,
    config: dict[str, Any] | None = None,
) -> ModuleState:
    """Validate and store a module's state. Raises ModuleStateError on rule violations
    and pydantic.ValidationError on an invalid config."""
    states = await load_states(conn, modules)
    current = states[key]
    new_enabled = current.enabled if enabled is None else enabled
    new_config = (
        current.config if config is None else current.module.config_schema.model_validate(config)
    )

    if new_enabled:
        off = [d for d in current.module.depends_on if d != "core" and not states[d].enabled]
        if off:
            raise ModuleStateError(f"enable {', '.join(off)} first")
    else:
        needed_by = [
            k for k, s in states.items() if s.enabled and k != key and key in s.module.depends_on
        ]
        if needed_by:
            raise ModuleStateError(f"{', '.join(needed_by)} depends on it")

    await conn.execute(
        "insert into tenant_module (module_key, enabled, config, version) values (%s, %s, %s, %s)"
        " on conflict (tenant_id, module_key) do update"
        " set enabled = excluded.enabled, config = excluded.config, version = excluded.version",
        (key, new_enabled, Jsonb(new_config.model_dump(mode="json")), current.module.version),
    )
    return ModuleState(current.module, new_enabled, new_config)
