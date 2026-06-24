import pytest

from invoicer.observability import estimate_cost


def test_estimate_cost_sonnet():
    # 1000 in + 1000 out @ (3, 15) USD/Mtok = 0.003 + 0.015
    assert estimate_cost("claude-sonnet-4-6", 1000, 1000) == pytest.approx(0.018)


def test_estimate_cost_opus():
    # 1000 in + 1000 out @ (5, 25) USD/Mtok = 0.005 + 0.025
    assert estimate_cost("claude-opus-4-8", 1000, 1000) == pytest.approx(0.030)


def test_estimate_cost_haiku():
    # 1000 in + 1000 out @ (1, 5) USD/Mtok = 0.001 + 0.005
    assert estimate_cost("claude-haiku-4-5", 1000, 1000) == pytest.approx(0.006)


def test_estimate_cost_unknown_model_is_zero():
    assert estimate_cost("model-ktorego-nie-ma", 1000, 1000) == 0.0


def test_estimate_cost_zero_tokens():
    assert estimate_cost("claude-sonnet-4-6", 0, 0) == 0.0
