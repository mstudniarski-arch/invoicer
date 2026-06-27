from invoicer.models import Citation
from invoicer.rag.eval import faithfulness_rate, mean, recall_at_k, reciprocal_rank
from invoicer.rag.models import RetrievedChunk


def test_recall_at_k_full_and_partial():
    assert recall_at_k(["a", "b", "c"], {"a", "b"}, k=3) == 1.0
    assert recall_at_k(["a", "x", "y"], {"a", "b"}, k=3) == 0.5
    assert recall_at_k(["x", "y", "a"], {"a"}, k=2) == 0.0  # 'a' poza top-2


def test_recall_at_k_empty_expected_is_one():
    assert recall_at_k(["a"], set(), k=1) == 1.0


def test_reciprocal_rank():
    assert reciprocal_rank(["x", "a", "y"], {"a"}) == 0.5  # 1/2
    assert reciprocal_rank(["a"], {"a"}) == 1.0
    assert reciprocal_rank(["x", "y"], {"a"}) == 0.0


def test_mean():
    assert mean([1.0, 0.0, 0.5]) == 0.5
    assert mean([]) == 0.0


_CHUNK = RetrievedChunk(
    source_id="s",
    article_ref="a",
    title="t",
    url="u",
    text="Miejscem swiadczenia uslug jest siedziba uslugobiorcy.",
)


def test_faithfulness_rate_counts_supported_citations():
    supported = Citation(source_id="s", article_ref="a", quoted_span="Miejscem swiadczenia uslug")
    fabricated = Citation(source_id="s", article_ref="a", quoted_span="zdanie spoza zrodla")
    rate = faithfulness_rate([supported, fabricated], [_CHUNK])
    assert rate == 0.5


def test_faithfulness_rate_no_citations_is_zero():
    assert faithfulness_rate([], [_CHUNK]) == 0.0
