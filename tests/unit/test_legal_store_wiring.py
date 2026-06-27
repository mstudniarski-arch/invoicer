from invoicer.adapters.in_memory_legal_store import InMemoryLegalStore
from invoicer.ports import LegalKnowledgeStore
from invoicer.runner import build_legal_store


def test_falls_back_to_in_memory_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    store = build_legal_store()
    assert isinstance(store, InMemoryLegalStore)
    assert isinstance(store, LegalKnowledgeStore)
    assert store.search("cokolwiek") == []  # pusty store -> abstention w grafie
