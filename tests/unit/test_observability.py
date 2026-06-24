import pytest

from invoicer.observability import LlmCall, LlmMetrics, estimate_cost


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


def test_metrics_record_and_totals():
    m = LlmMetrics()
    m.record(LlmCall("claude-sonnet-4-6", 100, 20, 0.0006, 500))
    m.record(LlmCall("claude-sonnet-4-6", 200, 30, 0.0011, 700))
    t = m.totals()
    assert t["n_calls"] == 2
    assert t["input_tokens"] == 300
    assert t["output_tokens"] == 50
    assert t["total_tokens"] == 350
    assert t["latency_ms"] == 1200
    assert t["cost_usd"] == pytest.approx(0.0017)


def test_metrics_empty_totals():
    t = LlmMetrics().totals()
    assert t["n_calls"] == 0
    assert t["input_tokens"] == 0
    assert t["output_tokens"] == 0
    assert t["total_tokens"] == 0
    assert t["cost_usd"] == 0
    assert t["latency_ms"] == 0
