import os

import pytest

from invoicer.adapters.voyage_embedder import VoyageEmbedder

pytestmark = pytest.mark.skipif(
    not os.getenv("VOYAGE_API_KEY"), reason="brak VOYAGE_API_KEY — test live pominiety"
)


def test_live_embedding_shape_and_determinism():
    emb = VoyageEmbedder()
    a = emb.embed_query("import uslug — art. 28b")
    b = emb.embed_query("import uslug — art. 28b")
    assert len(a) == 1024
    assert a == b  # to samo zapytanie -> ten sam wektor
