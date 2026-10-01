"""Generate docs/ERD.md (Mermaid ER diagrams) from the live database schema.

    uv run python scripts/generate_erd.py          # write docs/ERD.md
    uv run python scripts/generate_erd.py --check  # exit 1 if it is out of date

tests/tenancy/test_data_model.py runs the same generator against a freshly migrated
database, so the committed ERD always matches what the migrations produce.
"""

import sys
from collections import defaultdict
from pathlib import Path

import psycopg

from confluo_core.settings import get_settings

OUT = Path(__file__).resolve().parents[1] / "docs" / "ERD.md"

HEADER = """# Confluo — Entity relationship diagram

Generated from the migrated schema by `scripts/generate_erd.py`; do not edit by hand
(`make erd` regenerates it, and a test fails when it's stale).

Every table except `tenant` and `app_user` has a `tenant_id` referencing `tenant`;
those links are left out to keep the diagrams readable. Columns marked FK are part
of a composite `(tenant_id, id)` foreign key where the target is tenant-scoped.
"""

COLUMNS = """
select c.table_name, c.column_name, c.udt_name, c.is_nullable = 'YES', c.ordinal_position
from information_schema.columns c
join information_schema.tables t using (table_schema, table_name)
where c.table_schema = 'public' and t.table_type = 'BASE TABLE'
  and c.table_name <> 'alembic_version'
order by c.table_name, c.ordinal_position
"""

KEYS = """
select con.conrelid::regclass::text, con.contype, con.confrelid::regclass::text,
       array(select a.attname from unnest(con.conkey) k
             join pg_attribute a on a.attrelid = con.conrelid and a.attnum = k)::text[]
from pg_constraint con
join pg_namespace n on n.oid = con.connamespace
where n.nspname = 'public' and con.contype in ('p', 'f')
order by 1, 2, 3, 4
"""


def render(conn: psycopg.Connection) -> str:
    columns: dict[str, list[tuple[str, str, bool]]] = defaultdict(list)
    for table, column, udt, nullable, _ in conn.execute(COLUMNS).fetchall():
        columns[table].append(
            (column, udt.lstrip("_") + ("[]" if udt.startswith("_") else ""), nullable)
        )

    pk: dict[str, set[str]] = defaultdict(set)
    fk_cols: dict[str, set[str]] = defaultdict(set)
    edges: set[tuple[str, str, str]] = set()
    for table, kind, target, cols in conn.execute(KEYS).fetchall():
        if kind == "p":
            pk[table].update(cols)
            continue
        own = [c for c in cols if c != "tenant_id"] or cols
        if target == "tenant":
            continue
        fk_cols[table].update(own)
        edges.add((target, table, ", ".join(own)))

    def diagram(title: str, tables: list[str]) -> str:
        lines = [f"## {title}", "", "```mermaid", "erDiagram"]
        for t in tables:
            lines.append(f"  {t} {{")
            for name, typ, nullable in columns[t]:
                marks = ",".join(
                    m for m, on in (("PK", name in pk[t]), ("FK", name in fk_cols[t])) if on
                )
                suffix = f" {marks}" if marks else ""
                note = ' "nullable"' if nullable and not marks else ""
                lines.append(f"    {typ.replace(' ', '_')} {name}{suffix}{note}")
            lines.append("  }")
        names = set(tables)
        for parent, child, label in sorted(edges):
            if child in names:
                lines.append(f'  {parent} ||--o{{ {child} : "{label}"')
        lines += ["```", ""]
        return "\n".join(lines)

    core = sorted(t for t in columns if not t.startswith("crm_"))
    crm = sorted(t for t in columns if t.startswith("crm_"))
    return "\n".join([HEADER, diagram("Core", core), diagram("CRM module", crm)])


def main() -> None:
    with psycopg.connect(str(get_settings().migrations_database_url)) as conn:
        text = render(conn)
    if "--check" in sys.argv:
        if OUT.read_text() != text:
            raise SystemExit("docs/ERD.md is out of date; run `make erd`")
        print("docs/ERD.md is up to date")
        return
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
