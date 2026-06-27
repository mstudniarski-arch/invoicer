import os

import pytest

from invoicer.adapters.fake_embedder import DeterministicEmbedder
from invoicer.adapters.pgvector_store import PgVectorLegalStore
from invoicer.rag.corpus import Chunk
from invoicer.rag.ingest import ingest_corpus

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="brak DATABASE_URL — test live pominiety"
)


def _chunk(source_id, text):
    return Chunk(
        source_id=source_id,
        article_ref=source_id,
        title=source_id,
        url="u",
        kind="ustawa",
        text=text,
    )


def test_ingest_then_search_roundtrip():
    # Uzywamy DeterministicEmbedder (dim=8) zeby test nie zalezal od VOYAGE_API_KEY.
    embedder = DeterministicEmbedder(dim=8)
    store = PgVectorLegalStore(embedder, dim=8)
    chunks = [_chunk("a", "import uslug art 28b"), _chunk("b", "wnt art 9")]
    ingest_corpus(chunks, embedder, store)
    # idempotencja na poziomie DB
    assert ingest_corpus(chunks, embedder, store) == 0
    results = store.search("import uslug art 28b", k=1)
    assert results and results[0].source_id == "a"
