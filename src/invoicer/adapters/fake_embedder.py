from __future__ import annotations

import hashlib
import math


class DeterministicEmbedder:
    """Powtarzalny embedder do CI: tekst -> znormalizowany wektor z hasza.

    BEZ semantyki, ale w pelni deterministyczny: identyczny tekst -> identyczny wektor
    (cosine = 1.0). Pozwala pisac deterministyczne testy retrievalu bez sieci/DB —
    zapytanie rowne tresci chunka trafia na pierwsze miejsce.
    """

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    def _vector(self, text: str) -> list[float]:
        out: list[float] = []
        counter = 0
        while len(out) < self._dim:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            for i in range(0, len(digest), 4):
                if len(out) >= self._dim:
                    break
                n = int.from_bytes(digest[i : i + 4], "big")
                out.append((n / 2**32) * 2 - 1)  # [-1, 1)
            counter += 1
        norm = math.sqrt(sum(x * x for x in out)) or 1.0
        return [x / norm for x in out]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)
