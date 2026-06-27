from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not (os.getenv("VOYAGE_API_KEY") and os.getenv("DATABASE_URL")),
    reason="eval RAG wymaga VOYAGE_API_KEY + DATABASE_URL — pominiety",
)


def test_retrieval_recall_meets_bar():
    from scripts.run_evals import evaluate_retrieval

    summary = evaluate_retrieval(k=5)
    # Po zindeksowaniu korpusu realne embeddingi powinny trafiac oczekiwane artykuly.
    assert summary["recall_at_k"] >= 0.7
    assert summary["mrr"] >= 0.5
