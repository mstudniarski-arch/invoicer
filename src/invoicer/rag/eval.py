from __future__ import annotations


def recall_at_k(retrieved_refs: list[str], expected_refs: set[str], k: int) -> float:
    """Ulamek oczekiwanych article_ref obecnych w top-k zwroconych. Pusty expected -> 1.0."""
    if not expected_refs:
        return 1.0
    top = set(retrieved_refs[:k])
    return len(top & expected_refs) / len(expected_refs)


def reciprocal_rank(retrieved_refs: list[str], expected_refs: set[str]) -> float:
    """1/pozycja pierwszego trafionego oczekiwanego ref (1-indexed); 0.0 gdy brak."""
    for position, ref in enumerate(retrieved_refs, start=1):
        if ref in expected_refs:
            return 1.0 / position
    return 0.0


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
