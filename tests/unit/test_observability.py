import logging

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from invoicer.observability import LlmCall, LlmMetrics, LlmMetricsCallback, estimate_cost


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


def _llm_result(usage):
    """Syntetyczny LLMResult z wiadomoscia czatu; usage=None -> brak usage_metadata."""
    if usage is None:
        msg = AIMessage(content="ok")
    else:
        # langchain_core wymaga total_tokens w usage_metadata — uzupelniamy jezeli brakuje
        full_usage = dict(usage)
        if "total_tokens" not in full_usage:
            full_usage["total_tokens"] = full_usage.get("input_tokens", 0) + full_usage.get(
                "output_tokens", 0
            )
        msg = AIMessage(content="ok", usage_metadata=full_usage)
    return LLMResult(generations=[[ChatGeneration(message=msg)]])


def test_callback_records_call_with_tokens_cost_latency():
    metrics = LlmMetrics()
    clock = iter([10.0, 11.5]).__next__  # start=10.0, end=11.5 -> 1500 ms
    cb = LlmMetricsCallback(metrics, model="claude-sonnet-4-6", clock=clock)
    cb.on_chat_model_start({}, [], run_id="r1")
    cb.on_llm_end(
        _llm_result({"input_tokens": 654, "output_tokens": 35, "total_tokens": 689}),
        run_id="r1",
    )
    assert len(metrics.calls) == 1
    call = metrics.calls[0]
    assert call.model == "claude-sonnet-4-6"
    assert call.input_tokens == 654
    assert call.output_tokens == 35
    assert call.latency_ms == 1500
    assert call.cost_usd == pytest.approx(654 / 1_000_000 * 3 + 35 / 1_000_000 * 15)


def test_callback_handles_missing_usage_metadata():
    metrics = LlmMetrics()
    clock = iter([0.0, 0.25]).__next__
    cb = LlmMetricsCallback(metrics, model="claude-sonnet-4-6", clock=clock)
    cb.on_chat_model_start({}, [], run_id="r2")
    cb.on_llm_end(_llm_result(None), run_id="r2")
    call = metrics.calls[0]
    assert call.input_tokens == 0
    assert call.output_tokens == 0
    assert call.cost_usd == 0.0
    assert call.latency_ms == 250


def test_callback_via_on_llm_start_path():
    # sciezka non-chat: on_llm_start zamiast on_chat_model_start
    metrics = LlmMetrics()
    clock = iter([1.0, 2.0]).__next__
    cb = LlmMetricsCallback(metrics, model="claude-sonnet-4-6", clock=clock)
    cb.on_llm_start({}, ["prompt"], run_id="r4")
    cb.on_llm_end(_llm_result({"input_tokens": 10, "output_tokens": 5}), run_id="r4")
    assert metrics.calls[0].latency_ms == 1000


def test_callback_aggregates_multiple_calls():
    metrics = LlmMetrics()
    clock = iter([0.0, 1.0, 5.0, 6.0]).__next__
    cb = LlmMetricsCallback(metrics, model="claude-sonnet-4-6", clock=clock)
    cb.on_chat_model_start({}, [], run_id="a")
    cb.on_llm_end(_llm_result({"input_tokens": 100, "output_tokens": 10}), run_id="a")
    cb.on_chat_model_start({}, [], run_id="b")
    cb.on_llm_end(_llm_result({"input_tokens": 200, "output_tokens": 20}), run_id="b")
    t = metrics.totals()
    assert t["n_calls"] == 2
    assert t["input_tokens"] == 300
    assert t["output_tokens"] == 30


def test_callback_logs_metrics_without_pii(caplog):
    metrics = LlmMetrics()
    clock = iter([0.0, 0.1]).__next__
    cb = LlmMetricsCallback(metrics, model="claude-sonnet-4-6", clock=clock)
    with caplog.at_level(logging.INFO, logger="invoicer.metrics"):
        cb.on_chat_model_start({}, [], run_id="r3")
        cb.on_llm_end(_llm_result({"input_tokens": 654, "output_tokens": 35}), run_id="r3")
    text = caplog.text
    assert "llm_call" in text
    assert "model=claude-sonnet-4-6" in text
    assert "input_tokens=654" in text
    assert "output_tokens=35" in text
    assert "cost_usd=" in text
    assert "latency_ms=" in text


def test_callback_cleans_up_start_on_error():
    # blad LLM: on_llm_error sprzata start, brak wpisu w kolektorze, brak wycieku run_id
    metrics = LlmMetrics()
    cb = LlmMetricsCallback(metrics, model="claude-sonnet-4-6", clock=iter([1.0]).__next__)
    cb.on_chat_model_start({}, [], run_id="err1")
    cb.on_llm_error(RuntimeError("boom"), run_id="err1")
    assert metrics.calls == []
    assert cb._starts == {}
