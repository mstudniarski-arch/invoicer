import os

import psycopg
import pytest

from invoicer.adapters.fake_embedder import DeterministicEmbedder
from invoicer.adapters.pgvector_store import PgVectorLegalStore
from invoicer.rag.corpus import Chunk
from invoicer.rag.ingest import ingest_corpus

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="brak DATABASE_URL — test live pominiety"
)

_TABLE = "legal_chunks_live_test"


def _reset_table():
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn:
        conn.execute(f"DROP TABLE IF EXISTS {_TABLE}")


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
    _reset_table()
    embedder = DeterministicEmbedder(dim=8)
    store = PgVectorLegalStore(embedder, dim=8, table=_TABLE)
    chunks = [_chunk("a", "import uslug art 28b"), _chunk("b", "wnt art 9")]
    ingest_corpus(chunks, embedder, store)
    # idempotencja na poziomie DB
    assert ingest_corpus(chunks, embedder, store) == 0
    results = store.search("import uslug art 28b", k=1)
    assert results and results[0].source_id == "a"
