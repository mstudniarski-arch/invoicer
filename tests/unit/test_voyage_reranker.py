from invoicer.adapters.voyage_reranker import VoyageReranker
from invoicer.ports import Reranker


class _Result:
    def __init__(self, index, relevance_score):
        self.index = index
        self.relevance_score = relevance_score


class _Reranked:
    def __init__(self, results):
        self.results = results


class _FakeVoyage:
    def __init__(self):
        self.calls = []

    def rerank(self, query, documents, model, top_k):
        self.calls.append((query, tuple(documents), model, top_k))
        # odwraca kolejnosc: ostatni dokument najtrafniejszy
        ranked = list(range(len(documents)))[::-1][:top_k]
        return _Reranked([_Result(i, 1.0 - n * 0.1) for n, i in enumerate(ranked)])


def test_satisfies_reranker_protocol():
    assert isinstance(VoyageReranker(client=_FakeVoyage()), Reranker)


def test_rerank_returns_indices_and_scores_in_order():
    fake = _FakeVoyage()
    out = VoyageReranker(client=fake, model="rerank-2.5").rerank("q", ["d0", "d1", "d2"], top_k=2)
    assert [idx for idx, _ in out] == [2, 1]  # odwrocona kolejnosc, top-2
    assert out[0][1] == 1.0
    assert fake.calls[0][2] == "rerank-2.5"
