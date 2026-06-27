from invoicer.adapters.voyage_embedder import VoyageEmbedder
from invoicer.ports import Embedder


class _Result:
    def __init__(self, embeddings):
        self.embeddings = embeddings


class _FakeVoyage:
    def __init__(self):
        self.calls = []

    def embed(self, texts, model, input_type):
        self.calls.append((tuple(texts), model, input_type))
        return _Result([[0.1, 0.2, 0.3] for _ in texts])


def test_satisfies_embedder_protocol():
    assert isinstance(VoyageEmbedder(client=_FakeVoyage()), Embedder)


def test_embed_documents_uses_document_input_type():
    fake = _FakeVoyage()
    out = VoyageEmbedder(client=fake, model="voyage-3-large").embed_documents(["a", "b"])
    assert out == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert fake.calls[0] == (("a", "b"), "voyage-3-large", "document")


def test_embed_query_uses_query_input_type_and_returns_single_vector():
    fake = _FakeVoyage()
    vec = VoyageEmbedder(client=fake).embed_query("import uslug")
    assert vec == [0.1, 0.2, 0.3]
    assert fake.calls[0][2] == "query"
