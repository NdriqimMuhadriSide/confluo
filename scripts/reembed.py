"""Re-index every published knowledge item (all tenants), e.g. after changing
CONFLUO_LLM_EMBEDDING: vectors of different models can't be compared, so search
ignores chunks of another model until they're rebuilt.

    make reembed
"""

import asyncio

import psycopg

from confluo_core.db import create_pool
from confluo_core.job_app import build_job_app
from confluo_core.modules import discover_modules
from confluo_core.settings import get_settings


async def main() -> None:
    settings = get_settings()
    # Listing across tenants needs the owner role (RLS would hide other tenants).
    with psycopg.connect(str(settings.migrations_database_url)) as conn:
        items = conn.execute(
            "select tenant_id, id from crm_knowledge_item where published"
        ).fetchall()
    app = build_job_app(discover_modules())
    pool = create_pool(settings)
    await pool.open()
    try:
        async with app.open_async(pool):
            task = app.tasks["crm:embed_knowledge_item"]
            for tenant_id, item_id in items:
                await task.defer_async(tenant_id=str(tenant_id), item_id=str(item_id))
    finally:
        await pool.close()
    print(f"queued {len(items)} items for re-embedding with {settings.llm_embedding}")


if __name__ == "__main__":
    asyncio.run(main())
