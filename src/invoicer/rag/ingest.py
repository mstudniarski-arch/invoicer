from __future__ import annotations

from typing import Protocol

from invoicer.ports import Embedder
from invoicer.rag.corpus import Chunk


class WritableStore(Protocol):
    """Kontrakt zapisu dla ingestu (spelniaja go InMemoryLegalStore i PgVectorLegalStore)."""

    def existing_hashes(self) -> set[str]: ...

    def add(self, content_hash: str, embedding: list[float], chunk: Chunk) -> None: ...


def ingest_corpus(chunks: list[Chunk], embedder: Embedder, store: WritableStore) -> int:
    """Idempotentny ingest: pomija chunki o znanym content_hash, embeduje i zapisuje tylko nowe.

    Zwraca liczbe nowo dodanych chunkow. Embedding liczony WYLACZNIE dla nowych (oszczednosc).
    """
    existing = store.existing_hashes()
    new_chunks = [c for c in chunks if c.content_hash not in existing]
    if not new_chunks:
        return 0
    vectors = embedder.embed_documents([c.text for c in new_chunks])
    for chunk, vector in zip(new_chunks, vectors, strict=True):
        store.add(chunk.content_hash, vector, chunk)
    return len(new_chunks)
