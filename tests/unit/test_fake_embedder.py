import math

from invoicer.adapters.fake_embedder import DeterministicEmbedder
from invoicer.ports import Embedder


def test_satisfies_embedder_protocol():
    assert isinstance(DeterministicEmbedder(), Embedder)


def test_dimension_and_unit_norm():
    emb = DeterministicEmbedder(dim=32)
    vec = emb.embed_query("art. 28b")
    assert len(vec) == 32
    assert math.isclose(math.sqrt(sum(x * x for x in vec)), 1.0, rel_tol=1e-9)


def test_same_text_same_vector():
    emb = DeterministicEmbedder(dim=64)
    assert emb.embed_query("import uslug") == emb.embed_query("import uslug")


def test_different_text_different_vector():
    emb = DeterministicEmbedder(dim=64)
    assert emb.embed_query("import uslug") != emb.embed_query("wnt")
