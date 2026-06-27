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
    for c in chunks:
        assert c.text.strip()
        assert c.article_ref
        assert c.url.startswith("http")
