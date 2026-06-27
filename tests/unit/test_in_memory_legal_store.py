from invoicer.adapters.fake_embedder import DeterministicEmbedder
from invoicer.adapters.in_memory_legal_store import InMemoryLegalStore
from invoicer.ports import LegalKnowledgeStore
from invoicer.rag.corpus import Chunk


def _chunk(source_id, text):
    return Chunk(
        source_id=source_id,
        article_ref=source_id,
        title=source_id,
        url="u",
        kind="ustawa",
        text=text,
    )


def test_satisfies_legal_store_protocol():
    store = InMemoryLegalStore.from_chunks([], DeterministicEmbedder(dim=32))
    assert isinstance(store, LegalKnowledgeStore)


def test_empty_store_returns_no_results():
    store = InMemoryLegalStore.from_chunks([], DeterministicEmbedder(dim=32))
    assert store.search("cokolwiek", k=5) == []


def test_exact_match_ranks_first():
    chunks = [_chunk("a", "import uslug art 28b"), _chunk("b", "wnt art 9")]
    store = InMemoryLegalStore.from_chunks(chunks, DeterministicEmbedder(dim=64))
    results = store.search("import uslug art 28b", k=2)
    assert results[0].source_id == "a"  # zapytanie == tresc chunka 'a' -> cosine 1.0 -> pierwszy
    assert results[0].score > 0.99


def test_k_limits_results():
    chunks = [_chunk(str(i), f"tekst {i}") for i in range(5)]
    store = InMemoryLegalStore.from_chunks(chunks, DeterministicEmbedder(dim=32))
    assert len(store.search("tekst 0", k=2)) == 2


def test_add_is_idempotent_by_content_hash():
    store = InMemoryLegalStore(DeterministicEmbedder(dim=32))
    chunk = _chunk("a", "powtorka")
    store.add(chunk.content_hash, [0.0] * 32, chunk)
    store.add(chunk.content_hash, [0.0] * 32, chunk)
    assert store.existing_hashes() == {chunk.content_hash}
