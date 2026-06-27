from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Chunk:
    """Pojedynczy fragment korpusu prawnego (akapit) z metadanymi pliku zrodlowego."""

    source_id: str
    article_ref: str
    title: str
    url: str
    kind: str
    text: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_markdown(raw: str) -> tuple[dict[str, str], str]:
    """Rozdziela frontmatter (--- ... ---) od tresci. Zwraca (metadane, body)."""
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, raw
    meta: dict[str, str] = {}
    idx = 1
    while idx < len(lines) and lines[idx].strip() != "---":
        if ":" in lines[idx]:
            key, _, value = lines[idx].partition(":")
            meta[key.strip()] = _strip_quotes(value)
        idx += 1
    body = "\n".join(lines[idx + 1 :])
    return meta, body


def load_corpus(directory: Path) -> list[Chunk]:
    """Wczytuje *.md z katalogu, dzieli body na akapity (puste pominiete) -> lista Chunk."""
    chunks: list[Chunk] = []
    for path in sorted(directory.glob("*.md")):
        meta, body = parse_markdown(path.read_text(encoding="utf-8"))
        for para in (p.strip() for p in body.split("\n\n")):
            if not para:
                continue
            chunks.append(
                Chunk(
                    source_id=meta.get("source_id", path.stem),
                    article_ref=meta.get("article_ref", ""),
                    title=meta.get("title", ""),
                    url=meta.get("url", ""),
                    kind=meta.get("kind", ""),
                    text=para,
                )
            )
    return chunks
