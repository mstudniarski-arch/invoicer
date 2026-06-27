from __future__ import annotations

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
        citations=[
            Citation(source_id="vat-art-28b", article_ref="art. 28b ust. 1", quoted_span="x")
        ],
        grounding_status=GroundingStatus.WEAK,
    )
    assert c.citations[0].article_ref == "art. 28b ust. 1"
    assert c.grounding_status == "weak"
