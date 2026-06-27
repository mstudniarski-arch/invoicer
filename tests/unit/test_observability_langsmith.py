from __future__ import annotations

import os

from invoicer.observability_langsmith import init_langsmith


def test_init_langsmith_noop_without_key(monkeypatch):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    assert init_langsmith() is False
    assert "LANGCHAIN_TRACING_V2" not in os.environ


def test_init_langsmith_enables_with_key(monkeypatch):
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGCHAIN_PROJECT", raising=False)
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-test")
    assert init_langsmith() is True
    assert os.environ["LANGCHAIN_TRACING_V2"] == "true"
    assert os.environ["LANGCHAIN_PROJECT"] == "invoicer"
