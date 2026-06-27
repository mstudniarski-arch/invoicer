"""Live eval harness dla legal-grounded RAG. Wymaga VOYAGE_API_KEY + DATABASE_URL.

Uruchom: PYTHONPATH=.:src VOYAGE_API_KEY=... DATABASE_URL=... ANTHROPIC_API_KEY=... \
    uv run python scripts/run_evals.py
Zaklada zindeksowany korpus (scripts/ingest_legal_corpus.py).
"""

from __future__ import annotations

from pathlib import Path

from invoicer.adapters.pgvector_store import PgVectorLegalStore
from invoicer.adapters.voyage_embedder import VoyageEmbedder
from invoicer.adapters.voyage_reranker import VoyageReranker
from invoicer.rag.eval import (
    build_invoice_from_case,
    load_cases,
    mean,
    recall_at_k,
    reciprocal_rank,
)
from invoicer.rag.query import build_retrieval_query

_CASES = Path(__file__).resolve().parents[1] / "data" / "evals" / "legal_cases.jsonl"
_REPORT = Path(__file__).resolve().parents[1] / "docs" / "evals" / "legal-rag-report.md"


def _store() -> PgVectorLegalStore:
    return PgVectorLegalStore(VoyageEmbedder(), reranker=VoyageReranker())


def evaluate_retrieval(k: int = 5) -> dict:
    store = _store()
    cases = load_cases(_CASES)
    recalls, rrs = [], []
    for case in cases:
        query = build_retrieval_query(build_invoice_from_case(case))
        refs = [c.article_ref for c in store.search(query, k=k)]
        expected = set(case["expected_article_refs"])
        recalls.append(recall_at_k(refs, expected, k=k))
        rrs.append(reciprocal_rank(refs, expected))
    return {"k": k, "n": len(cases), "recall_at_k": mean(recalls), "mrr": mean(rrs)}


def write_report(retrieval: dict) -> None:
    _REPORT.parent.mkdir(parents=True, exist_ok=True)
    _REPORT.write_text(
        "# Legal-Grounded RAG — Eval Report\n\n"
        f"- Cases: **{retrieval['n']}**\n"
        f"- Recall@{retrieval['k']}: **{retrieval['recall_at_k']:.2f}**\n"
        f"- MRR: **{retrieval['mrr']:.2f}**\n\n"
        "_Wygenerowane przez `scripts/run_evals.py` (Voyage + pgvector + Claude)._\n",
        encoding="utf-8",
    )


def main() -> None:
    retrieval = evaluate_retrieval(k=5)
    print(retrieval)
    write_report(retrieval)
    print(f"Raport: {_REPORT}")


if __name__ == "__main__":
    main()
