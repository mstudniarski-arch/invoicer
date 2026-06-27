from invoicer.adapters.fake_embedder import DeterministicEmbedder
from invoicer.adapters.pgvector_store import PgVectorLegalStore
from invoicer.ports import LegalKnowledgeStore


def test_satisfies_legal_store_protocol_without_connecting():
    # Konstrukcja nie laczy sie z baza (lazy) — sam ksztalt protokolu wystarcza.
    store = PgVectorLegalStore(DeterministicEmbedder(dim=8), dsn="postgresql://unused")
    assert isinstance(store, LegalKnowledgeStore)
    assert hasattr(store, "existing_hashes") and hasattr(store, "add")
