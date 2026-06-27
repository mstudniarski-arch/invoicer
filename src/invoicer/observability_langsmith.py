from __future__ import annotations

import os


def init_langsmith() -> bool:
    """Wlacza tracing LangSmith gdy ustawiony LANGSMITH_API_KEY (lub LANGCHAIN_API_KEY).

    LangChain/LangGraph auto-traceuja gdy LANGCHAIN_TRACING_V2=true + klucz w env — wiec tu tylko
    ustawiamy wlacznik i domyslny projekt (idempotentnie). Przebiegi sa nazywane/tagowane thread_id
    w runner._run_config, dzieki czemu w LangSmith kazda faktura to osobny, nawigowalny
    trace (prompty/odpowiedzi/retrieved chunks/routing). No-op bez klucza. Zwraca True gdy wlaczone.
    """
    key = os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")
    if not key:
        return False
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_API_KEY", key)
    os.environ.setdefault("LANGCHAIN_PROJECT", os.getenv("LANGSMITH_PROJECT", "invoicer"))
    return True
