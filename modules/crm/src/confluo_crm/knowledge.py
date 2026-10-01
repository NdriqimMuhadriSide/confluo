"""Knowledge base indexing and hybrid search (ARCHITECTURE.md §3, crm_knowledge_*).

Publishing an item enqueues `crm:embed_knowledge_item` in the same transaction. The
job splits the item into chunks, embeds them through the LLM gateway (embedding
tier, input_type "document") and replaces the item's chunks. Search embeds the
question (input_type "query") and merges vector similarity with full-text ranking
by reciprocal rank fusion, over published items only.
"""

import re
from dataclasses import dataclass
from uuid import UUID

from psycopg import AsyncConnection

from confluo_core.jobs import LLM_KEY, JobDeps, TaskSet, tenant_task
from confluo_core.llm import LLMGateway

crm_tasks = TaskSet()

CHUNK_CHARS = 800  # roughly 150-200 tokens; FAQ answers usually fit in one chunk
RRF_K = 60  # standard reciprocal-rank-fusion constant
CANDIDATES = 20


def chunk_text(title: str, body: str, max_chars: int = CHUNK_CHARS) -> list[str]:
    """Split on paragraphs, then sentences, keeping chunks under `max_chars`.
    Each chunk starts with the item title so it stands on its own in search."""
    pieces: list[str] = []
    for para in re.split(r"\n\s*\n", body.strip()):
        para = " ".join(para.split())
        if not para:
            continue
        if len(para) <= max_chars:
            pieces.append(para)
            continue
        sentences = re.split(r"(?<=[.!?])\s+", para)
        current = ""
        for s in sentences:
            while len(s) > max_chars:  # one enormous sentence: hard cut
                if current:
                    pieces.append(current)
                    current = ""
                pieces.append(s[:max_chars])
                s = s[max_chars:]
            if current and len(current) + 1 + len(s) > max_chars:
                pieces.append(current)
                current = s
            else:
                current = f"{current} {s}".strip()
        if current:
            pieces.append(current)

    chunks: list[str] = []
    current = ""
    for p in pieces:  # merge small paragraphs up to the limit
        if current and len(current) + 2 + len(p) > max_chars:
            chunks.append(current)
            current = p
        else:
            current = f"{current}\n\n{p}".strip()
    if current:
        chunks.append(current)
    return [f"{title}\n{c}" for c in chunks] or [title]


@tenant_task(crm_tasks, name="embed_knowledge_item", pass_deps=True)
async def embed_knowledge_item(conn: AsyncConnection, deps: JobDeps, item_id: str) -> None:
    llm: LLMGateway = deps.context[LLM_KEY]
    cur = await conn.execute(
        "select title, body, language, published from crm_knowledge_item where id = %s", (item_id,)
    )
    row = await cur.fetchone()
    await conn.execute("delete from crm_knowledge_chunk where knowledge_item_id = %s", (item_id,))
    if row is None or not row[3]:
        return  # deleted or unpublished meanwhile: no chunks
    title, body, language, _ = row
    chunks = chunk_text(title, body)
    result = await llm.embed(deps.tenant_id, "kb_index", chunks, "document")
    for position, (content, vector) in enumerate(zip(chunks, result.vectors, strict=True)):
        await conn.execute(
            "insert into crm_knowledge_chunk"
            " (knowledge_item_id, language, position, content, embedding, embedding_model)"
            " values (%s, %s, %s, %s, %s::extensions.vector, %s)",
            (item_id, language, position, content, _vector(vector), result.model),
        )
    await conn.execute(
        "update crm_knowledge_item set embedded_at = now() where id = %s", (item_id,)
    )


def any_word_query(text: str) -> str:
    """A tsquery matching chunks that share any word with the question. Questions are
    natural language ("Is there free parking?"), so requiring every word (as
    plainto/websearch_to_tsquery do) would miss almost everything; ts_rank_cd still
    ranks chunks matching more words higher."""
    words = sorted({w for w in re.findall(r"\w+", text.lower()) if len(w) > 1})
    return " | ".join(words)


def _vector(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.7f}" for v in values) + "]"


@dataclass(frozen=True)
class Hit:
    chunk_id: UUID
    item_id: UUID
    kind: str
    title: str
    language: str
    content: str
    score: float  # reciprocal rank fusion; higher is better
    vector_rank: int | None
    text_rank: int | None


async def hybrid_search(
    conn: AsyncConnection,
    llm: LLMGateway,
    tenant_id: UUID,
    query: str,
    *,
    language: str | None = None,
    limit: int = 5,
) -> list[Hit]:
    embedded = await llm.embed(tenant_id, "kb_search", [query], "query")
    vec = _vector(embedded.vectors[0])
    cur = await conn.execute(
        """
        with chunks as (
          select c.id, c.knowledge_item_id, c.content, c.language, c.embedding, c.tsv
          from crm_knowledge_chunk c
          join crm_knowledge_item i on i.id = c.knowledge_item_id
          where c.tenant_id = %(t)s and i.published
            and c.embedding_model = %(model)s
            and (%(lang)s::text is null or c.language = %(lang)s)
        ),
        by_vector as (
          select id, row_number() over (
            order by embedding operator(extensions.<=>) %(vec)s::extensions.vector) as rank
          from chunks order by rank limit %(n)s
        ),
        by_text as (
          select id, row_number() over (
            order by ts_rank_cd(tsv, to_tsquery('simple', %(q)s)) desc) as rank
          from chunks where %(q)s <> '' and tsv @@ to_tsquery('simple', %(q)s)
          order by rank limit %(n)s
        ),
        fused as (
          select coalesce(v.id, x.id) as id, v.rank as vrank, x.rank as xrank,
            coalesce(1.0 / (%(k)s + v.rank), 0) + coalesce(1.0 / (%(k)s + x.rank), 0) as score
          from by_vector v full join by_text x on x.id = v.id
        )
        select f.id, c.knowledge_item_id, i.kind, i.title, c.language, c.content,
               f.score, f.vrank, f.xrank
        from fused f join chunks c on c.id = f.id
        join crm_knowledge_item i on i.id = c.knowledge_item_id
        order by f.score desc limit %(limit)s
        """,
        {
            "t": tenant_id,
            "model": embedded.model,
            "lang": language,
            "vec": vec,
            "q": any_word_query(query),
            "n": CANDIDATES,
            "k": RRF_K,
            "limit": limit,
        },
    )
    return [
        Hit(
            chunk_id=r[0],
            item_id=r[1],
            kind=r[2],
            title=r[3],
            language=r[4],
            content=r[5],
            score=float(r[6]),
            vector_rank=r[7],
            text_rank=r[8],
        )
        for r in await cur.fetchall()
    ]
