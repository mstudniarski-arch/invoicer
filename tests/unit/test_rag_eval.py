from invoicer.rag.eval import mean, recall_at_k, reciprocal_rank


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
