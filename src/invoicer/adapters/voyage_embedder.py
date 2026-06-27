from __future__ import annotations

from typing import Any

_DEFAULT_MODEL = "voyage-3-large"


class VoyageEmbedder:
    """Embedder oparty o Voyage AI (partner Anthropic). Klient tworzony leniwie (CI: fake).

    Domyslny model: voyage-3-large (1024-dim, wielojezyczny). Alternatywa domenowa: voyage-law-2
    (rozstrzyga eval recall@k w Planie 03).
    """

    def __init__(self, *, model: str = _DEFAULT_MODEL, client: Any = None) -> None:
        self._model = model
        self._client = client

    def _voyage(self) -> Any:
        if self._client is None:
            import voyageai

            self._client = voyageai.Client()  # czyta VOYAGE_API_KEY ze srodowiska
        return self._client

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._voyage().embed(texts, model=self._model, input_type="document").embeddings

    def embed_query(self, text: str) -> list[float]:
        return self._voyage().embed([text], model=self._model, input_type="query").embeddings[0]
