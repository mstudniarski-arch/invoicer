from invoicer.adapters.fake_embedder import DeterministicEmbedder
from invoicer.adapters.in_memory_legal_store import InMemoryLegalStore
from invoicer.rag.corpus import Chunk
from invoicer.rag.ingest import ingest_corpus


def _chunk(text):
    return Chunk(source_id="a", article_ref="a1", title="A", url="u", kind="ustawa", text=text)


def test_ingest_adds_all_new_chunks():
    embedder = DeterministicEmbedder(dim=32)
    store = InMemoryLegalStore(embedder)
    n = ingest_corpus([_chunk("x"), _chunk("y")], embedder, store)
    assert n == 2
    assert store.search("x", k=2)  # cos sie zindeksowalo


def test_ingest_is_idempotent():
    embedder = DeterministicEmbedder(dim=32)
    store = InMemoryLegalStore(embedder)
    chunks = [_chunk("x"), _chunk("y")]
    assert ingest_corpus(chunks, embedder, store) == 2
    assert ingest_corpus(chunks, embedder, store) == 0  # nic nowego -> brak ponownego embeddingu
    assert len(store.existing_hashes()) == 2


def test_ingest_adds_only_the_new_one():
    embedder = DeterministicEmbedder(dim=32)
    store = InMemoryLegalStore(embedder)
    ingest_corpus([_chunk("x")], embedder, store)
    n = ingest_corpus([_chunk("x"), _chunk("z")], embedder, store)
    assert n == 1
