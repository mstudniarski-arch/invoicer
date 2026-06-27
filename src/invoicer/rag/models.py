from __future__ import annotations

from pydantic import BaseModel


class RetrievedChunk(BaseModel):
    """Fragment prawny zwrocony z bazy wektorowej (wynik retrievalu)."""

    source_id: str
    article_ref: str
    title: str
    url: str
    text: str
    score: float = 0.0
