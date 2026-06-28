from __future__ import annotations

import os
from typing import Any

from invoicer.ports import Embedder
from invoicer.rag.corpus import Chunk
from invoicer.rag.models import RetrievedChunk

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
    content_hash TEXT PRIMARY KEY,
    source_id    TEXT NOT NULL,
    article_ref  TEXT NOT NULL,
    title        TEXT NOT NULL,
    url          TEXT NOT NULL,
    kind         TEXT NOT NULL,
    text         TEXT NOT NULL,
    embedding    vector({dim}) NOT NULL
);
"""


class PgVectorLegalStore:
    """Wektorowy store w Postgres/pgvector. Polaczenie leniwe (CI uzywa InMemoryLegalStore).

    Implementuje kontrakt zapisu (existing_hashes/add) dla ingest_corpus oraz port search().
    """

    def __init__(
        self,
        embedder: Embedder,
        *,
        dsn: str | None = None,
        dim: int = 1024,
        conn: Any = None,
        table: str = "legal_chunks",
        reranker: Any = None,
        fetch_n: int = 20,
    ) -> None:
        if not table.replace("_", "").isalnum():
            raise ValueError(f"Niedozwolona nazwa tabeli: {table!r}")
        self._embedder = embedder
        self._dsn = dsn
        self._dim = dim
        self._conn = conn
        self._table = table
        self._reranker = reranker
        self._fetch_n = fetch_n

    def _connection(self) -> Any:
        if self._conn is None:
            import psycopg
            from pgvector.psycopg import register_vector

            conn = psycopg.connect(self._dsn or os.environ["DATABASE_URL"], autocommit=True)
            # KOLEJNOSC ma znaczenie: rozszerzenie musi istniec ZANIM register_vector odpyta
            # baze o typ `vector` — inaczej na swiezej bazie (nowy projekt Supabase) register
            # wywalilby sie, zanim DDL zdazyl utworzyc rozszerzenie.
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            register_vector(conn)
            conn.execute(_TABLE_DDL.format(table=self._table, dim=self._dim))
            self._conn = conn
        return self._conn

    def existing_hashes(self) -> set[str]:
        rows = self._connection().execute(f"SELECT content_hash FROM {self._table}").fetchall()
        return {r[0] for r in rows}

    def add(self, content_hash: str, embedding: list[float], chunk: Chunk) -> None:
        from pgvector.psycopg import Vector

        self._connection().execute(
            f"INSERT INTO {self._table} "
            "(content_hash, source_id, article_ref, title, url, kind, text, embedding) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (content_hash) DO NOTHING",
            (
                content_hash,
                chunk.source_id,
                chunk.article_ref,
                chunk.title,
                chunk.url,
                chunk.kind,
                chunk.text,
                Vector(embedding),
            ),
        )

    def search(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        from pgvector.psycopg import Vector

        q = Vector(self._embedder.embed_query(query))
        limit = self._fetch_n if self._reranker else k
        rows = (
            self._connection()
            .execute(
                "SELECT source_id, article_ref, title, url, text, "
                "1 - (embedding <=> %s) AS score "
                f"FROM {self._table} ORDER BY embedding <=> %s LIMIT %s",
                (q, q, limit),
            )
            .fetchall()
        )
        chunks = [
            RetrievedChunk(
                source_id=r[0], article_ref=r[1], title=r[2], url=r[3], text=r[4], score=r[5]
            )
            for r in rows
        ]
        if not self._reranker or not chunks:
            return chunks[:k]
        order = self._reranker.rerank(query, [c.text for c in chunks], top_k=k)
        return [chunks[idx].model_copy(update={"score": score}) for idx, score in order]
