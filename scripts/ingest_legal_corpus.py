"""Ingest kurowanego korpusu prawnego (data/legal) do pgvector.

Uruchom:
    PYTHONPATH=src VOYAGE_API_KEY=... DATABASE_URL=... uv run python scripts/ingest_legal_corpus.py
"""

from __future__ import annotations

from pathlib import Path

from invoicer.adapters.pgvector_store import PgVectorLegalStore
from invoicer.adapters.voyage_embedder import VoyageEmbedder
from invoicer.rag.corpus import load_corpus
from invoicer.rag.ingest import ingest_corpus

_LEGAL_DIR = Path(__file__).resolve().parents[1] / "data" / "legal"


def main() -> None:
    chunks = load_corpus(_LEGAL_DIR)
    embedder = VoyageEmbedder()
    store = PgVectorLegalStore(embedder)
    added = ingest_corpus(chunks, embedder, store)
    print(f"Zindeksowano {added} nowych chunkow (korpus: {len(chunks)}).")


if __name__ == "__main__":
    main()
