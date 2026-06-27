# Legal-Grounded RAG — Plan 01: Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the offline-testable retrieval foundation for legal-grounded RAG — domain models, ports, fakes, corpus loader, idempotent ingest, and the real Voyage + pgvector adapters — with zero changes to the LangGraph pipeline yet.

**Architecture:** Two new ports (`Embedder`, `LegalKnowledgeStore`) in the existing ports-and-adapters style. A `DeterministicEmbedder` + `InMemoryLegalStore` make retrieval fully reproducible in CI (no network, no DB); the real `VoyageEmbedder` + `PgVectorLegalStore` are injected the same way the Claude adapters are, with live-gated smoke tests. A curated, git-versioned legal corpus is loaded/chunked and ingested idempotently (content-hash dedup, mirroring `processed.py`/`ledger.py`).

**Tech Stack:** Python 3.12 · uv · Pydantic v2 · `voyageai` (embeddings) · `psycopg` + `pgvector` (vector store) · pytest · ruff.

---

## Decomposition (this is Plan 1 of 3)

This plan is derived from [`docs/superpowers/specs/2026-06-27-legal-grounded-rag-design.md`](../specs/2026-06-27-legal-grounded-rag-design.md). The spec's 6 milestones are split into 3 plans:

- **Plan 01 — Foundation (THIS PLAN):** spec milestone 1 — models, ports, fakes, corpus, ingest, real Voyage + pgvector adapters. Produces a working, tested retrieval layer usable standalone (`store.search(...)`), independent of the graph.
- **Plan 02 — Graph integration + corrective:** spec milestones 2–4 — `retrieve_legal_context` → grounded `reason_exception` → `verify_grounding` nodes, routing, abstention, confidence caps, injection-continuity test.
- **Plan 03 — Evals + deploy + docs:** spec milestones 5–6 — retrieval/faithfulness/ablation eval harness, Fly Postgres deploy, README/diagram.

**Implementation refinement vs spec §3/§9 (deliberate, flag for veto):** this plan uses the `voyageai` SDK and `psycopg`+`pgvector` **directly** rather than the `langchain-voyageai` / `langchain-postgres` wrappers. Reason: it keeps the `Embedder`/`LegalKnowledgeStore` ports clean and transparent (you can read the exact cosine query), and avoids forcing LangChain's `Embeddings` adapter onto our port. LangChain remains showcased via `langchain-anthropic` + LangGraph. If you prefer the langchain wrappers, swap them inside the two real adapters — the ports and tests are unaffected.

---

## File Structure

**Create:**
- `src/invoicer/rag/__init__.py` — package marker.
- `src/invoicer/rag/models.py` — `RetrievedChunk` (pure Pydantic, no `invoicer` imports → no import cycle).
- `src/invoicer/rag/corpus.py` — `Chunk` dataclass + `load_corpus()` / frontmatter parsing / paragraph chunking.
- `src/invoicer/rag/ingest.py` — `ingest_corpus()` idempotent load→embed→add.
- `src/invoicer/adapters/fake_embedder.py` — `DeterministicEmbedder` (hash→unit vector).
- `src/invoicer/adapters/in_memory_legal_store.py` — `InMemoryLegalStore` (cosine, incremental add).
- `src/invoicer/adapters/voyage_embedder.py` — `VoyageEmbedder` (real, lazy client).
- `src/invoicer/adapters/pgvector_store.py` — `PgVectorLegalStore` (real, lazy psycopg conn).
- `scripts/ingest_legal_corpus.py` — CLI: load `data/legal/` → embed (Voyage) → upsert (pgvector).
- `data/legal/*.md` — curated legal corpus (frontmatter + statutory text).
- Tests: `tests/unit/test_rag_models.py`, `test_fake_embedder.py`, `test_rag_corpus.py`, `test_in_memory_legal_store.py`, `test_rag_ingest.py`, `test_voyage_embedder.py`, `test_pgvector_store.py`; `tests/live/test_voyage_embedder_live.py`, `test_pgvector_store_live.py`; `tests/live/fixtures/` reuse.

**Modify:**
- `src/invoicer/models.py` — add `GroundingStatus`, `Citation`; add `citations` + `grounding_status` to `Classification` (additive defaults; existing equality-based tests stay green).
- `src/invoicer/ports.py` — add `Embedder` + `LegalKnowledgeStore` protocols.
- `pyproject.toml` — add `voyageai`, `psycopg[binary]`, `pgvector` to `[project].dependencies`.

**Not touched in this plan:** `state.py`, `graph/*`, `adapters/claude_reasoner.py` (those change in Plan 02).

---

## Task 1: Domain models (`Citation`, `GroundingStatus`, `RetrievedChunk`)

**Files:**
- Create: `src/invoicer/rag/__init__.py`, `src/invoicer/rag/models.py`
- Modify: `src/invoicer/models.py`
- Test: `tests/unit/test_rag_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_rag_models.py
from invoicer.models import Citation, Classification, CountryBucket, GroundingStatus, TaxTreatment
from invoicer.rag.models import RetrievedChunk


def test_retrieved_chunk_defaults_score_zero():
    chunk = RetrievedChunk(
        source_id="vat-art-28b",
        article_ref="art. 28b ust. 1",
        title="Ustawa o VAT — art. 28b",
        url="https://isap.sejm.gov.pl/x",
        text="Miejscem swiadczenia uslug...",
    )
    assert chunk.score == 0.0


def test_classification_grounding_defaults_are_additive():
    # Istniejacy kod tworzy Classification bez nowych pol — domyslne wartosci nie psuja rownosci.
    a = Classification(treatment=TaxTreatment.KRAJOWA, country_bucket=CountryBucket.PL)
    b = Classification(treatment=TaxTreatment.KRAJOWA, country_bucket=CountryBucket.PL)
    assert a == b
    assert a.citations == []
    assert a.grounding_status == GroundingStatus.GROUNDED


def test_classification_accepts_citations_and_status():
    c = Classification(
        treatment=TaxTreatment.IMPORT_USLUG,
        country_bucket=CountryBucket.POZA_UE,
        citations=[Citation(source_id="vat-art-28b", article_ref="art. 28b ust. 1", quoted_span="x")],
        grounding_status=GroundingStatus.WEAK,
    )
    assert c.citations[0].article_ref == "art. 28b ust. 1"
    assert c.grounding_status == "weak"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_rag_models.py -v`
Expected: FAIL — `ModuleNotFoundError: invoicer.rag` / `ImportError: cannot import name 'Citation'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/invoicer/rag/__init__.py
```
(empty file)

```python
# src/invoicer/rag/models.py
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
```

In `src/invoicer/models.py`, add to the imports line `from enum import StrEnum` (already present) and append at the end of the file:

```python
class GroundingStatus(StrEnum):
    GROUNDED = "grounded"
    WEAK = "weak"
    UNSUPPORTED = "unsupported"


class Citation(BaseModel):
    """Cytat podstawy prawnej w uzasadnieniu klasyfikacji (sprawdzany w verify_grounding)."""

    source_id: str
    article_ref: str
    quoted_span: str
```

Then add two fields to the existing `Classification` model (after `currency_note`):

```python
    citations: list[Citation] = Field(default_factory=list)
    grounding_status: GroundingStatus = GroundingStatus.GROUNDED
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_rag_models.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest -q`
Expected: all previously-passing tests still pass (additive defaults don't break equality-based node tests).

- [ ] **Step 6: Commit**

```bash
cd /Users/mski/Developer/Invoicer
git add src/invoicer/rag/__init__.py src/invoicer/rag/models.py src/invoicer/models.py tests/unit/test_rag_models.py
git commit -m "feat(rag): RetrievedChunk + Citation/GroundingStatus on Classification"
```

---

## Task 2: RAG ports (`Embedder`, `LegalKnowledgeStore`)

**Files:**
- Modify: `src/invoicer/ports.py`
- Test: `tests/unit/test_ports.py` (append) — or a new `tests/unit/test_rag_ports.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_rag_ports.py
from invoicer.ports import Embedder, LegalKnowledgeStore
from invoicer.rag.models import RetrievedChunk


class _Emb:
    def embed_documents(self, texts):
        return [[0.0] for _ in texts]

    def embed_query(self, text):
        return [0.0]


class _Store:
    def search(self, query, k=5):
        return [RetrievedChunk(source_id="s", article_ref="a", title="t", url="u", text="x")]


def test_embedder_protocol_is_runtime_checkable():
    assert isinstance(_Emb(), Embedder)


def test_legal_store_protocol_is_runtime_checkable():
    assert isinstance(_Store(), LegalKnowledgeStore)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_rag_ports.py -v`
Expected: FAIL — `ImportError: cannot import name 'Embedder'`.

- [ ] **Step 3: Write minimal implementation**

In `src/invoicer/ports.py`, add the import (below the existing model import) and the two protocols at the end of the file:

```python
from invoicer.rag.models import RetrievedChunk
```

```python
@runtime_checkable
class Embedder(Protocol):
    """Zamienia tekst na wektory. Rozroznia dokument (ingest) i zapytanie (retrieval)."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


@runtime_checkable
class LegalKnowledgeStore(Protocol):
    """Baza wektorowa przepisow: zwraca k najtrafniejszych fragmentow dla zapytania."""

    def search(self, query: str, k: int = 5) -> list[RetrievedChunk]: ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_rag_ports.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/mski/Developer/Invoicer
git add src/invoicer/ports.py tests/unit/test_rag_ports.py
git commit -m "feat(rag): Embedder + LegalKnowledgeStore ports"
```

---

## Task 3: `DeterministicEmbedder` (offline fake)

**Files:**
- Create: `src/invoicer/adapters/fake_embedder.py`
- Test: `tests/unit/test_fake_embedder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_fake_embedder.py
import math

from invoicer.adapters.fake_embedder import DeterministicEmbedder
from invoicer.ports import Embedder


def test_satisfies_embedder_protocol():
    assert isinstance(DeterministicEmbedder(), Embedder)


def test_dimension_and_unit_norm():
    emb = DeterministicEmbedder(dim=32)
    vec = emb.embed_query("art. 28b")
    assert len(vec) == 32
    assert math.isclose(math.sqrt(sum(x * x for x in vec)), 1.0, rel_tol=1e-9)


def test_same_text_same_vector():
    emb = DeterministicEmbedder(dim=64)
    assert emb.embed_query("import uslug") == emb.embed_query("import uslug")


def test_different_text_different_vector():
    emb = DeterministicEmbedder(dim=64)
    assert emb.embed_query("import uslug") != emb.embed_query("wnt")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_fake_embedder.py -v`
Expected: FAIL — `ModuleNotFoundError: invoicer.adapters.fake_embedder`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/invoicer/adapters/fake_embedder.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_fake_embedder.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/mski/Developer/Invoicer
git add src/invoicer/adapters/fake_embedder.py tests/unit/test_fake_embedder.py
git commit -m "feat(rag): DeterministicEmbedder fake for offline CI"
```

---

## Task 4: Corpus loader + chunker (`rag/corpus.py`)

**Files:**
- Create: `src/invoicer/rag/corpus.py`
- Test: `tests/unit/test_rag_corpus.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_rag_corpus.py
from invoicer.rag.corpus import Chunk, load_corpus


def _write(dir_path, name, body):
    (dir_path / name).write_text(body, encoding="utf-8")


def test_loads_frontmatter_and_paragraph_chunks(tmp_path):
    _write(
        tmp_path,
        "vat-art-28b.md",
        '---\n'
        'source_id: vat-art-28b\n'
        'article_ref: "art. 28b ust. 1"\n'
        'title: "Ustawa o VAT - art. 28b"\n'
        'url: "https://isap.sejm.gov.pl/x"\n'
        'kind: ustawa\n'
        '---\n'
        "Pierwszy akapit przepisu.\n\nDrugi akapit przepisu.\n",
    )
    chunks = load_corpus(tmp_path)
    assert len(chunks) == 2
    assert all(isinstance(c, Chunk) for c in chunks)
    assert chunks[0].source_id == "vat-art-28b"
    assert chunks[0].article_ref == "art. 28b ust. 1"  # cudzyslowy usuniete
    assert chunks[0].title == "Ustawa o VAT - art. 28b"
    assert chunks[0].kind == "ustawa"
    assert chunks[0].text == "Pierwszy akapit przepisu."
    assert chunks[1].text == "Drugi akapit przepisu."


def test_content_hash_is_stable_and_text_derived(tmp_path):
    _write(
        tmp_path,
        "a.md",
        '---\nsource_id: a\narticle_ref: a1\ntitle: A\nurl: u\nkind: ustawa\n---\nTresc.\n',
    )
    [chunk] = load_corpus(tmp_path)
    assert chunk.content_hash == Chunk(
        source_id="a", article_ref="a1", title="A", url="u", kind="ustawa", text="Tresc."
    ).content_hash


def test_ignores_blank_paragraphs(tmp_path):
    _write(
        tmp_path,
        "a.md",
        '---\nsource_id: a\narticle_ref: a1\ntitle: A\nurl: u\nkind: ustawa\n---\n\nX\n\n\n\nY\n\n',
    )
    chunks = load_corpus(tmp_path)
    assert [c.text for c in chunks] == ["X", "Y"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_rag_corpus.py -v`
Expected: FAIL — `ModuleNotFoundError: invoicer.rag.corpus`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/invoicer/rag/corpus.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_rag_corpus.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/mski/Developer/Invoicer
git add src/invoicer/rag/corpus.py tests/unit/test_rag_corpus.py
git commit -m "feat(rag): corpus loader + frontmatter/paragraph chunker"
```

---

## Task 5: `InMemoryLegalStore` (offline cosine search)

**Files:**
- Create: `src/invoicer/adapters/in_memory_legal_store.py`
- Test: `tests/unit/test_in_memory_legal_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_in_memory_legal_store.py
from invoicer.adapters.fake_embedder import DeterministicEmbedder
from invoicer.adapters.in_memory_legal_store import InMemoryLegalStore
from invoicer.ports import LegalKnowledgeStore
from invoicer.rag.corpus import Chunk


def _chunk(source_id, text):
    return Chunk(source_id=source_id, article_ref=source_id, title=source_id, url="u",
                 kind="ustawa", text=text)


def test_satisfies_legal_store_protocol():
    store = InMemoryLegalStore.from_chunks([], DeterministicEmbedder(dim=32))
    assert isinstance(store, LegalKnowledgeStore)


def test_empty_store_returns_no_results():
    store = InMemoryLegalStore.from_chunks([], DeterministicEmbedder(dim=32))
    assert store.search("cokolwiek", k=5) == []


def test_exact_match_ranks_first():
    chunks = [_chunk("a", "import uslug art 28b"), _chunk("b", "wnt art 9")]
    store = InMemoryLegalStore.from_chunks(chunks, DeterministicEmbedder(dim=64))
    results = store.search("import uslug art 28b", k=2)
    assert results[0].source_id == "a"  # zapytanie == tresc chunka 'a' -> cosine 1.0 -> pierwszy
    assert results[0].score > 0.99


def test_k_limits_results():
    chunks = [_chunk(str(i), f"tekst {i}") for i in range(5)]
    store = InMemoryLegalStore.from_chunks(chunks, DeterministicEmbedder(dim=32))
    assert len(store.search("tekst 0", k=2)) == 2


def test_add_is_idempotent_by_content_hash():
    store = InMemoryLegalStore(DeterministicEmbedder(dim=32))
    chunk = _chunk("a", "powtorka")
    store.add(chunk.content_hash, [0.0] * 32, chunk)
    store.add(chunk.content_hash, [0.0] * 32, chunk)
    assert store.existing_hashes() == {chunk.content_hash}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_in_memory_legal_store.py -v`
Expected: FAIL — `ModuleNotFoundError: invoicer.adapters.in_memory_legal_store`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/invoicer/adapters/in_memory_legal_store.py
from __future__ import annotations

import math

from invoicer.ports import Embedder
from invoicer.rag.corpus import Chunk
from invoicer.rag.models import RetrievedChunk


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class InMemoryLegalStore:
    """Wektorowy store w pamieci (cosine brute-force) — fake do CI i lokalnych testow.

    Implementuje kontrakt zapisu uzywany przez ingest_corpus: existing_hashes() + add(),
    oraz port LegalKnowledgeStore: search().
    """

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._rows: list[tuple[str, list[float], Chunk]] = []  # (content_hash, vector, chunk)
        self._hashes: set[str] = set()

    @classmethod
    def from_chunks(cls, chunks: list[Chunk], embedder: Embedder) -> InMemoryLegalStore:
        store = cls(embedder)
        vectors = embedder.embed_documents([c.text for c in chunks]) if chunks else []
        for chunk, vector in zip(chunks, vectors, strict=True):
            store.add(chunk.content_hash, vector, chunk)
        return store

    def existing_hashes(self) -> set[str]:
        return set(self._hashes)

    def add(self, content_hash: str, embedding: list[float], chunk: Chunk) -> None:
        if content_hash in self._hashes:
            return
        self._hashes.add(content_hash)
        self._rows.append((content_hash, embedding, chunk))

    def search(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        if not self._rows:
            return []
        q = self._embedder.embed_query(query)
        scored = [(_cosine(q, vector), chunk) for _, vector, chunk in self._rows]
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            RetrievedChunk(
                source_id=chunk.source_id,
                article_ref=chunk.article_ref,
                title=chunk.title,
                url=chunk.url,
                text=chunk.text,
                score=score,
            )
            for score, chunk in scored[:k]
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_in_memory_legal_store.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/mski/Developer/Invoicer
git add src/invoicer/adapters/in_memory_legal_store.py tests/unit/test_in_memory_legal_store.py
git commit -m "feat(rag): InMemoryLegalStore cosine search + idempotent add"
```

---

## Task 6: Idempotent ingest (`rag/ingest.py`)

**Files:**
- Create: `src/invoicer/rag/ingest.py`
- Test: `tests/unit/test_rag_ingest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_rag_ingest.py
from invoicer.adapters.fake_embedder import DeterministicEmbedder
from invoicer.adapters.in_memory_legal_store import InMemoryLegalStore
from invoicer.rag.corpus import Chunk
from invoicer.rag.ingest import ingest_corpus


def _chunk(text):
    return Chunk(source_id="a", article_ref="a1", title="A", url="u", kind="ustawa", text=text)


def test_ingest_adds_all_new_chunks():
    embedder = DeterministicEmbedder(dim=32)
    store = InMemoryLegalStore(embedder)
    n = ingest_corpus([_chunk("x"), _chunk("y")], embedder, store)
    assert n == 2
    assert store.search("x", k=2)  # cos sie zindeksowalo


def test_ingest_is_idempotent():
    embedder = DeterministicEmbedder(dim=32)
    store = InMemoryLegalStore(embedder)
    chunks = [_chunk("x"), _chunk("y")]
    assert ingest_corpus(chunks, embedder, store) == 2
    assert ingest_corpus(chunks, embedder, store) == 0  # nic nowego -> brak ponownego embeddingu
    assert len(store.existing_hashes()) == 2


def test_ingest_adds_only_the_new_one():
    embedder = DeterministicEmbedder(dim=32)
    store = InMemoryLegalStore(embedder)
    ingest_corpus([_chunk("x")], embedder, store)
    n = ingest_corpus([_chunk("x"), _chunk("z")], embedder, store)
    assert n == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_rag_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError: invoicer.rag.ingest`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/invoicer/rag/ingest.py
from __future__ import annotations

from typing import Protocol

from invoicer.ports import Embedder
from invoicer.rag.corpus import Chunk


class WritableStore(Protocol):
    """Kontrakt zapisu dla ingestu (spelniaja go InMemoryLegalStore i PgVectorLegalStore)."""

    def existing_hashes(self) -> set[str]: ...

    def add(self, content_hash: str, embedding: list[float], chunk: Chunk) -> None: ...


def ingest_corpus(chunks: list[Chunk], embedder: Embedder, store: WritableStore) -> int:
    """Idempotentny ingest: pomija chunki o znanym content_hash, embeduje i zapisuje tylko nowe.

    Zwraca liczbe nowo dodanych chunkow. Embedding liczony WYLACZNIE dla nowych (oszczednosc).
    """
    existing = store.existing_hashes()
    new_chunks = [c for c in chunks if c.content_hash not in existing]
    if not new_chunks:
        return 0
    vectors = embedder.embed_documents([c.text for c in new_chunks])
    for chunk, vector in zip(new_chunks, vectors, strict=True):
        store.add(chunk.content_hash, vector, chunk)
    return len(new_chunks)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_rag_ingest.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
cd /Users/mski/Developer/Invoicer
git add src/invoicer/rag/ingest.py tests/unit/test_rag_ingest.py
git commit -m "feat(rag): idempotent ingest_corpus (content-hash dedup)"
```

---

## Task 7: Curated legal corpus data (`data/legal/*.md`)

This task creates **data**, not code. The bodies must be the **verbatim statutory text from ISAP** (Polish legal acts are public "materiały urzędowe"). Frontmatter is given exactly; for each file, paste the actual article text from the `url` below the `---`. A faithful excerpt is provided for `art. 28b ust. 1` to anchor the format; expand the rest from the official source.

**Files:**
- Create: `data/legal/vat-art-28b.md`, `data/legal/vat-art-17-odwrotne.md`, `data/legal/vat-art-9-wnt.md`, `data/legal/vat-import-towarow.md` (+ optionally MF objaśnienia / KIS files later).
- Test: `tests/unit/test_legal_corpus_data.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_legal_corpus_data.py
from pathlib import Path

from invoicer.rag.corpus import load_corpus

_LEGAL_DIR = Path(__file__).resolve().parents[2] / "data" / "legal"

_REQUIRED_SOURCE_IDS = {
    "vat-art-28b",
    "vat-art-17-odwrotne",
    "vat-art-9-wnt",
    "vat-import-towarow",
}


def test_corpus_dir_exists():
    assert _LEGAL_DIR.is_dir(), "Brak katalogu data/legal"


def test_required_provisions_present_and_parse():
    chunks = load_corpus(_LEGAL_DIR)
    assert chunks, "Korpus pusty"
    source_ids = {c.source_id for c in chunks}
    assert _REQUIRED_SOURCE_IDS <= source_ids
    # kazdy chunk ma niepusta tresc i metadane
    for c in chunks:
        assert c.text.strip()
        assert c.article_ref
        assert c.url.startswith("http")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_legal_corpus_data.py -v`
Expected: FAIL — `assert _LEGAL_DIR.is_dir()` (directory missing).

- [ ] **Step 3: Create the corpus files**

Create `data/legal/vat-art-28b.md` (the excerpt below for ust. 1 is faithful; append ust. 2–4 verbatim from the URL, each as its own paragraph separated by a blank line):

```markdown
---
source_id: vat-art-28b
article_ref: "art. 28b ust. 1"
title: "Ustawa o VAT — art. 28b (miejsce świadczenia usług na rzecz podatnika)"
url: "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20040540535"
kind: ustawa
---
Miejscem świadczenia usług w przypadku świadczenia usług na rzecz podatnika jest miejsce, w którym podatnik będący usługobiorcą posiada siedzibę działalności gospodarczej, z zastrzeżeniem ust. 2–4 oraz art. 28e, art. 28f ust. 1 i 1a, art. 28g ust. 1, art. 28i, art. 28j ust. 1 i 2 oraz art. 28n.
```

Create `data/legal/vat-art-17-odwrotne.md` (paste the verbatim text of art. 17 ust. 1 pkt 4 — nabywca usług jako podatnik / odwrotne obciążenie — from the URL below the frontmatter):

```markdown
---
source_id: vat-art-17-odwrotne
article_ref: "art. 17 ust. 1 pkt 4"
title: "Ustawa o VAT — art. 17 ust. 1 pkt 4 (podatnik-nabywca, odwrotne obciążenie)"
url: "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20040540535"
kind: ustawa
---
<wklej tutaj treść art. 17 ust. 1 pkt 4 z ISAP — jeden akapit na ustęp/punkt>
```

Create `data/legal/vat-art-9-wnt.md` (verbatim art. 9 — wewnątrzwspólnotowe nabycie towarów):

```markdown
---
source_id: vat-art-9-wnt
article_ref: "art. 9 ust. 1"
title: "Ustawa o VAT — art. 9 (wewnątrzwspólnotowe nabycie towarów)"
url: "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20040540535"
kind: ustawa
---
<wklej tutaj treść art. 9 z ISAP>
```

Create `data/legal/vat-import-towarow.md` (verbatim art. 2 pkt 7 definicja importu towarów + relevant fragment of the import-VAT obligation):

```markdown
---
source_id: vat-import-towarow
article_ref: "art. 2 pkt 7"
title: "Ustawa o VAT — import towarów (definicja i obowiązek)"
url: "https://isap.sejm.gov.pl/isap.nsf/DocDetails.xsp?id=WDU20040540535"
kind: ustawa
---
<wklej tutaj definicję importu towarów (art. 2 pkt 7) z ISAP>
```

> The `<wklej ...>` markers are **data-sourcing instructions**, not code placeholders: the authoritative text lives at the cited ISAP URL and must be copied verbatim. The test below validates structure and presence, not legal wording.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_legal_corpus_data.py -v`
Expected: PASS once all four files exist with real text (each `<wklej ...>` replaced by actual statutory paragraphs).

- [ ] **Step 5: Commit**

```bash
cd /Users/mski/Developer/Invoicer
git add data/legal tests/unit/test_legal_corpus_data.py
git commit -m "feat(rag): curated PL VAT corpus (art. 28b, 17, 9 WNT, import)"
```

---

## Task 8: `VoyageEmbedder` (real adapter, lazy client)

**Files:**
- Create: `src/invoicer/adapters/voyage_embedder.py`
- Modify: `pyproject.toml` (add `voyageai`)
- Test: `tests/unit/test_voyage_embedder.py`, `tests/live/test_voyage_embedder_live.py`

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, under `[project] dependencies`, add (keep the list alphabetical-ish, matching the existing style):

```toml
    "voyageai>=0.3.2",
```

Run: `cd /Users/mski/Developer/Invoicer && uv sync`
Expected: resolves and installs `voyageai`.

- [ ] **Step 2: Write the failing unit test (offline, fake client)**

```python
# tests/unit/test_voyage_embedder.py
from invoicer.adapters.voyage_embedder import VoyageEmbedder
from invoicer.ports import Embedder


class _Result:
    def __init__(self, embeddings):
        self.embeddings = embeddings


class _FakeVoyage:
    def __init__(self):
        self.calls = []

    def embed(self, texts, model, input_type):
        self.calls.append((tuple(texts), model, input_type))
        return _Result([[0.1, 0.2, 0.3] for _ in texts])


def test_satisfies_embedder_protocol():
    assert isinstance(VoyageEmbedder(client=_FakeVoyage()), Embedder)


def test_embed_documents_uses_document_input_type():
    fake = _FakeVoyage()
    out = VoyageEmbedder(client=fake, model="voyage-3-large").embed_documents(["a", "b"])
    assert out == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert fake.calls[0] == (("a", "b"), "voyage-3-large", "document")


def test_embed_query_uses_query_input_type_and_returns_single_vector():
    fake = _FakeVoyage()
    vec = VoyageEmbedder(client=fake).embed_query("import uslug")
    assert vec == [0.1, 0.2, 0.3]
    assert fake.calls[0][2] == "query"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_voyage_embedder.py -v`
Expected: FAIL — `ModuleNotFoundError: invoicer.adapters.voyage_embedder`.

- [ ] **Step 4: Write minimal implementation**

```python
# src/invoicer/adapters/voyage_embedder.py
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_voyage_embedder.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Write the live-gated smoke test**

```python
# tests/live/test_voyage_embedder_live.py
import os

import pytest

from invoicer.adapters.voyage_embedder import VoyageEmbedder

pytestmark = pytest.mark.skipif(
    not os.getenv("VOYAGE_API_KEY"), reason="brak VOYAGE_API_KEY — test live pominiety"
)


def test_live_embedding_shape_and_determinism():
    emb = VoyageEmbedder()
    a = emb.embed_query("import uslug — art. 28b")
    b = emb.embed_query("import uslug — art. 28b")
    assert len(a) == 1024
    assert a == b  # to samo zapytanie -> ten sam wektor
```

- [ ] **Step 7: Run the live test to confirm it skips (no key in CI)**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/live/test_voyage_embedder_live.py -v`
Expected: SKIPPED (1 skipped) when `VOYAGE_API_KEY` is unset.

- [ ] **Step 8: Commit**

```bash
cd /Users/mski/Developer/Invoicer
git add pyproject.toml uv.lock src/invoicer/adapters/voyage_embedder.py tests/unit/test_voyage_embedder.py tests/live/test_voyage_embedder_live.py
git commit -m "feat(rag): VoyageEmbedder adapter (voyage-3-large) + live smoke"
```

---

## Task 9: `PgVectorLegalStore` + ingest CLI (real adapter, live-gated)

**Files:**
- Create: `src/invoicer/adapters/pgvector_store.py`, `scripts/ingest_legal_corpus.py`
- Modify: `pyproject.toml` (add `psycopg[binary]`, `pgvector`)
- Test: `tests/unit/test_pgvector_store.py`, `tests/live/test_pgvector_store_live.py`

- [ ] **Step 1: Add dependencies**

In `pyproject.toml` `[project] dependencies`, add:

```toml
    "pgvector>=0.3.6",
    "psycopg[binary]>=3.2",
```

Run: `cd /Users/mski/Developer/Invoicer && uv sync`
Expected: resolves and installs `psycopg`, `pgvector`.

- [ ] **Step 2: Write the failing unit test (structural — no DB)**

```python
# tests/unit/test_pgvector_store.py
from invoicer.adapters.fake_embedder import DeterministicEmbedder
from invoicer.adapters.pgvector_store import PgVectorLegalStore
from invoicer.ports import LegalKnowledgeStore


def test_satisfies_legal_store_protocol_without_connecting():
    # Konstrukcja nie laczy sie z baza (lazy) — sam ksztalt protokolu wystarcza.
    store = PgVectorLegalStore(DeterministicEmbedder(dim=8), dsn="postgresql://unused")
    assert isinstance(store, LegalKnowledgeStore)
    assert hasattr(store, "existing_hashes") and hasattr(store, "add")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_pgvector_store.py -v`
Expected: FAIL — `ModuleNotFoundError: invoicer.adapters.pgvector_store`.

- [ ] **Step 4: Write minimal implementation**

```python
# src/invoicer/adapters/pgvector_store.py
from __future__ import annotations

import json
import os
from typing import Any

from invoicer.ports import Embedder
from invoicer.rag.corpus import Chunk
from invoicer.rag.models import RetrievedChunk

_DDL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS legal_chunks (
    content_hash TEXT PRIMARY KEY,
    source_id    TEXT NOT NULL,
    article_ref  TEXT NOT NULL,
    title        TEXT NOT NULL,
    url          TEXT NOT NULL,
    kind         TEXT NOT NULL,
    text         TEXT NOT NULL,
    embedding    vector(%(dim)s) NOT NULL
);
"""


class PgVectorLegalStore:
    """Wektorowy store w Postgres/pgvector. Polaczenie leniwe (CI uzywa InMemoryLegalStore).

    Implementuje kontrakt zapisu (existing_hashes/add) dla ingest_corpus oraz port search().
    """

    def __init__(
        self, embedder: Embedder, *, dsn: str | None = None, dim: int = 1024, conn: Any = None
    ) -> None:
        self._embedder = embedder
        self._dsn = dsn
        self._dim = dim
        self._conn = conn

    def _connection(self) -> Any:
        if self._conn is None:
            import psycopg
            from pgvector.psycopg import register_vector

            self._conn = psycopg.connect(self._dsn or os.environ["DATABASE_URL"], autocommit=True)
            register_vector(self._conn)
            self._conn.execute(_DDL % {"dim": self._dim})
        return self._conn

    def existing_hashes(self) -> set[str]:
        rows = self._connection().execute("SELECT content_hash FROM legal_chunks").fetchall()
        return {r[0] for r in rows}

    def add(self, content_hash: str, embedding: list[float], chunk: Chunk) -> None:
        self._connection().execute(
            "INSERT INTO legal_chunks "
            "(content_hash, source_id, article_ref, title, url, kind, text, embedding) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (content_hash) DO NOTHING",
            (
                content_hash,
                chunk.source_id,
                chunk.article_ref,
                chunk.title,
                chunk.url,
                chunk.kind,
                chunk.text,
                json.dumps(embedding),
            ),
        )

    def search(self, query: str, k: int = 5) -> list[RetrievedChunk]:
        q = self._embedder.embed_query(query)
        rows = self._connection().execute(
            "SELECT source_id, article_ref, title, url, text, "
            "1 - (embedding <=> %s) AS score "
            "FROM legal_chunks ORDER BY embedding <=> %s LIMIT %s",
            (json.dumps(q), json.dumps(q), k),
        ).fetchall()
        return [
            RetrievedChunk(
                source_id=r[0], article_ref=r[1], title=r[2], url=r[3], text=r[4], score=r[5]
            )
            for r in rows
        ]
```

> Note: `json.dumps(embedding)` works because `register_vector` makes pgvector accept the text form `[..]`; with the vector registered you may also pass the Python list directly. Keeping `json.dumps` makes the SQL identical whether or not the codec is registered.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_pgvector_store.py -v`
Expected: PASS (1 passed).

- [ ] **Step 6: Write the ingest CLI**

```python
# scripts/ingest_legal_corpus.py
"""Ingest kurowanego korpusu prawnego (data/legal) do pgvector.

Uruchom: PYTHONPATH=src VOYAGE_API_KEY=... DATABASE_URL=... uv run python scripts/ingest_legal_corpus.py
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
```

- [ ] **Step 7: Write the live-gated integration test**

```python
# tests/live/test_pgvector_store_live.py
import os

import pytest

from invoicer.adapters.fake_embedder import DeterministicEmbedder
from invoicer.adapters.pgvector_store import PgVectorLegalStore
from invoicer.rag.corpus import Chunk
from invoicer.rag.ingest import ingest_corpus

pytestmark = pytest.mark.skipif(
    not os.getenv("DATABASE_URL"), reason="brak DATABASE_URL — test live pominiety"
)


def _chunk(source_id, text):
    return Chunk(source_id=source_id, article_ref=source_id, title=source_id, url="u",
                 kind="ustawa", text=text)


def test_ingest_then_search_roundtrip():
    # Uzywamy DeterministicEmbedder (dim=8) zeby test nie zalezal od VOYAGE_API_KEY.
    embedder = DeterministicEmbedder(dim=8)
    store = PgVectorLegalStore(embedder, dim=8)
    chunks = [_chunk("a", "import uslug art 28b"), _chunk("b", "wnt art 9")]
    ingest_corpus(chunks, embedder, store)
    # idempotencja na poziomie DB
    assert ingest_corpus(chunks, embedder, store) == 0
    results = store.search("import uslug art 28b", k=1)
    assert results and results[0].source_id == "a"
```

> Requires a reachable Postgres with the `vector` extension available (e.g. `docker run -e POSTGRES_PASSWORD=x -p 5432:5432 pgvector/pgvector:pg16`, then `export DATABASE_URL=postgresql://postgres:x@localhost:5432/postgres`). Run once locally to verify; CI skips it.

- [ ] **Step 8: Run unit + live tests (live skips without DATABASE_URL)**

Run: `cd /Users/mski/Developer/Invoicer && uv run pytest tests/unit/test_pgvector_store.py tests/live/test_pgvector_store_live.py -v`
Expected: 1 passed (unit), 1 skipped (live) without `DATABASE_URL`.

- [ ] **Step 9: Commit**

```bash
cd /Users/mski/Developer/Invoicer
git add pyproject.toml uv.lock src/invoicer/adapters/pgvector_store.py scripts/ingest_legal_corpus.py tests/unit/test_pgvector_store.py tests/live/test_pgvector_store_live.py
git commit -m "feat(rag): PgVectorLegalStore + ingest CLI (live-gated)"
```

---

## Final verification (whole plan)

- [ ] **Run the full suite + lint (CI parity)**

Run:
```bash
cd /Users/mski/Developer/Invoicer
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```
Expected: all unit tests pass; new live tests skip without `VOYAGE_API_KEY`/`DATABASE_URL`; ruff clean (line-length ≤100; `zip(..., strict=...)` used everywhere). If `ruff format --check` flags a new file, run `uv run ruff format .` and amend.

---

## Self-Review (author check against spec)

- **Spec coverage (milestone 1):** ports `Embedder`/`LegalKnowledgeStore` (Task 2 — spec §3) ✅; `RetrievedChunk`/`Citation`/`grounding_status` (Task 1 — spec §10) ✅; fakes for offline CI (Tasks 3,5 — spec §3) ✅; curated git-versioned corpus (Task 7 — spec §6) ✅; idempotent content-hash ingest (Task 6 — spec §6) ✅; real Voyage + pgvector adapters (Tasks 8,9 — spec §3,§9) ✅; ingest CLI (Task 9 — spec §6) ✅. Deferred to Plan 02/03 (explicitly): graph nodes, corrective/abstention, `state.py` fields, eval harness, deploy — out of this plan's scope by design.
- **Placeholder scan:** the only `<wklej ...>` markers are in Task 7 and are external **data-sourcing** instructions (verbatim statute from a cited URL), not code placeholders; the corpus test validates structure/presence, and a faithful `art. 28b ust. 1` excerpt is included to anchor format. All code steps contain complete, runnable code.
- **Type/name consistency:** `Chunk` (corpus) carries `kind` + `content_hash`; `RetrievedChunk` (retrieval result) has no `kind`/`content_hash` — intentional split. `existing_hashes()`/`add()` names match across `InMemoryLegalStore`, `PgVectorLegalStore`, and the `WritableStore` Protocol used by `ingest_corpus`. `embed_documents`/`embed_query` consistent across `DeterministicEmbedder`/`VoyageEmbedder` and the `Embedder` port. Vector dim 1024 consistent (DDL, VoyageEmbedder default, live embedding-shape assertion).

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-27-legal-grounded-rag-01-foundation.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
