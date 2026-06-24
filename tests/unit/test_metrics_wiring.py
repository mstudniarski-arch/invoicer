import langchain_anthropic
import pytest

from invoicer.adapters.claude_detector import ClaudeInvoiceDetector
from invoicer.adapters.claude_extractor import ClaudeVisionExtractor
from invoicer.adapters.claude_reasoner import ClaudeExceptionReasoner
from invoicer.observability import LlmMetrics, LlmMetricsCallback

_FACTORIES = [ClaudeVisionExtractor, ClaudeInvoiceDetector, ClaudeExceptionReasoner]


class _RecordingChat:
    """Podstawiany w miejsce ChatAnthropic — zapamietuje kwargs konstruktora."""

    last_kwargs: dict = {}

    def __init__(self, **kwargs):
        _RecordingChat.last_kwargs = kwargs

    def with_structured_output(self, schema):
        return self


def _cb() -> LlmMetricsCallback:
    return LlmMetricsCallback(LlmMetrics(), model="claude-sonnet-4-6")


@pytest.mark.parametrize("factory", _FACTORIES)
def test_adapter_passes_callbacks_to_chatanthropic(monkeypatch, factory):
    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", _RecordingChat)
    _RecordingChat.last_kwargs = {}  # brak forwardu -> glosny KeyError, nie ciche stale
    cb = _cb()
    factory(callbacks=[cb])._client()
    assert _RecordingChat.last_kwargs["model"] == "claude-sonnet-4-6"
    assert _RecordingChat.last_kwargs["callbacks"] == [cb]


@pytest.mark.parametrize("factory", _FACTORIES)
def test_adapter_default_callbacks_is_none(monkeypatch, factory):
    monkeypatch.setattr(langchain_anthropic, "ChatAnthropic", _RecordingChat)
    _RecordingChat.last_kwargs = {}  # brak forwardu -> glosny KeyError, nie ciche stale
    factory()._client()
    assert _RecordingChat.last_kwargs["callbacks"] is None
