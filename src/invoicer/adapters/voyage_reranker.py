from __future__ import annotations

from typing import Any

_DEFAULT_MODEL = "rerank-2.5"


class VoyageReranker:
    """Reranker Voyage (rerank-2.5). Klient leniwy (CI: fake)."""

    def __init__(self, *, model: str = _DEFAULT_MODEL, client: Any = None) -> None:
        self._model = model
        self._client = client

    def _voyage(self) -> Any:
        if self._client is None:
            import voyageai

            self._client = voyageai.Client()
        return self._client

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[tuple[int, float]]:
        reranked = self._voyage().rerank(query, documents, model=self._model, top_k=top_k)
        return [(r.index, r.relevance_score) for r in reranked.results]
