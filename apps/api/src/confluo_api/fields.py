"""Custom field definitions (booking fields on appointments, extra customer fields)."""

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from psycopg import errors
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, StringConstraints, model_validator

from confluo_core.deps import Tenant, TenantContext, requires

router = APIRouter()

ManageFields = Annotated[TenantContext, Depends(requires("core.fields.manage"))]

Entity = Literal["customer", "appointment"]
FieldType = Literal["text", "long_text", "number", "date", "boolean", "select", "phone", "email"]
PiiLevel = Literal["none", "personal", "sensitive"]
Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,62}$")]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
LANGS = ("en", "nl", "fr", "de", "sq")


class FieldIn(BaseModel):
    entity: Entity
    key: Key
    # Label per language; at least one. Keys must be dashboard languages.
    label_i18n: dict[str, Label] = Field(min_length=1)
    type: FieldType
    options: list[Label] = []
    pii_level: PiiLevel = "personal"
    position: int = 0

    @model_validator(mode="after")
    def _check(self) -> "FieldIn":
        unknown = set(self.label_i18n) - set(LANGS)
        if unknown:
            raise ValueError(f"unknown languages: {sorted(unknown)}")
        if self.type == "select" and not self.options:
            raise ValueError("a select field needs options")
        if self.type != "select" and self.options:
            raise ValueError("only select fields have options")
        return self


class FieldPatch(BaseModel):
    label_i18n: dict[str, Label] | None = None
    options: list[Label] | None = None
    pii_level: PiiLevel | None = None
    position: int | None = None
    archived: bool | None = None


class FieldOut(BaseModel):
    id: UUID
    entity: Entity
    key: str
    label_i18n: dict[str, str]
    type: FieldType
    options: list[str]
    pii_level: PiiLevel
    position: int
    archived: bool


COLUMNS = "id, entity, key, label_i18n, type, options, pii_level, position, archived_at is not null"


def _out(r: Any) -> FieldOut:
    return FieldOut(
        id=r[0],
        entity=r[1],
        key=r[2],
        label_i18n=r[3],
        type=r[4],
        options=r[5],
        pii_level=r[6],
        position=r[7],
        archived=r[8],
    )


@router.get("/api/fields", tags=["fields"], operation_id="listFields")
async def list_fields(
    tenant: Tenant, entity: Entity | None = None, include_archived: bool = Query(False)
) -> list[FieldOut]:
    cur = await tenant.conn.execute(
        f"select {COLUMNS} from field_definition where tenant_id = %(t)s"
        " and (%(e)s::text is null or entity = %(e)s)"
        " and (%(a)s or archived_at is null) order by entity, position, key",
        {"t": tenant.tenant_id, "e": entity, "a": include_archived},
    )
    return [_out(r) for r in await cur.fetchall()]


@router.post(
    "/api/fields", tags=["fields"], operation_id="createField", status_code=status.HTTP_201_CREATED
)
async def create_field(body: FieldIn, tenant: ManageFields) -> FieldOut:
    try:
        async with tenant.conn.transaction():
            cur = await tenant.conn.execute(
                "insert into field_definition"
                " (entity, key, label_i18n, type, options, pii_level, position)"
                f" values (%s, %s, %s, %s, %s, %s, %s) returning {COLUMNS}",
                (
                    body.entity,
                    body.key,
                    Jsonb(body.label_i18n),
                    body.type,
                    Jsonb(body.options),
                    body.pii_level,
                    body.position,
                ),
            )
    except errors.UniqueViolation:
        raise HTTPException(status.HTTP_409_CONFLICT, "A field with that key exists") from None
    return _out(await cur.fetchone())


@router.patch("/api/fields/{field_id}", tags=["fields"], operation_id="updateField")
async def update_field(field_id: UUID, body: FieldPatch, tenant: ManageFields) -> FieldOut:
    sets: dict[str, Any] = {}
    data = body.model_dump(exclude_unset=True)
    if "label_i18n" in data and data["label_i18n"] is not None:
        sets["label_i18n"] = Jsonb(data["label_i18n"])
    if "options" in data and data["options"] is not None:
        sets["options"] = Jsonb(data["options"])
    for k in ("pii_level", "position"):
        if data.get(k) is not None:
            sets[k] = data[k]
    clause = ", ".join(f"{k} = %({k})s" for k in sets)
    if "archived" in data and data["archived"] is not None:
        clause = ", ".join(
            filter(None, [clause, "archived_at = case when %(archived)s then now() end"])
        )
        sets["archived"] = data["archived"]
    if clause:
        await tenant.conn.execute(
            f"update field_definition set {clause} where id = %(id)s and tenant_id = %(t)s",
            {**sets, "id": field_id, "t": tenant.tenant_id},
        )
    cur = await tenant.conn.execute(
        f"select {COLUMNS} from field_definition where id = %s and tenant_id = %s",
        (field_id, tenant.tenant_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such field")
    return _out(row)


@router.delete(
    "/api/fields/{field_id}",
    tags=["fields"],
    operation_id="archiveField",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def archive_field(field_id: UUID, tenant: ManageFields) -> Response:
    """Archive rather than delete: past appointments keep their values."""
    cur = await tenant.conn.execute(
        "update field_definition set archived_at = now()"
        " where id = %s and tenant_id = %s and archived_at is null",
        (field_id, tenant.tenant_id),
    )
    if cur.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such field")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
