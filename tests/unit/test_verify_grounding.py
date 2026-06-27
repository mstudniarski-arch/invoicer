from __future__ import annotations

from invoicer.graph.nodes import CONFIDENCE_CAP_UNSUPPORTED, make_verify_grounding_node
from invoicer.models import Citation, Classification, CountryBucket, GroundingStatus, TaxTreatment
from invoicer.rag.models import RetrievedChunk

_CHUNK = RetrievedChunk(
    source_id="vat-art-28b",
    article_ref="art. 28b ust. 1",
    title="t",
    url="u",
    text="Miejscem swiadczenia uslug na rzecz podatnika jest siedziba uslugobiorcy.",
)


def _classification(citations, status=GroundingStatus.GROUNDED, confidence=0.85):
    return Classification(
        treatment=TaxTreatment.IMPORT_USLUG,
        country_bucket=CountryBucket.POZA_UE,
        confidence=confidence,
        citations=citations,
        grounding_status=status,
    )


def test_supported_citation_marks_grounded():
    cit = Citation(
        source_id="vat-art-28b",
        article_ref="art. 28b ust. 1",
        quoted_span="Miejscem swiadczenia uslug na rzecz podatnika",
    )
    node = make_verify_grounding_node()
    update = node({"classification": _classification([cit]), "legal_context": [_CHUNK]})
    assert update["classification"].grounding_status == GroundingStatus.GROUNDED
    assert update["classification"].confidence == 0.85  # bez capa


def test_fabricated_span_marks_unsupported_and_caps_confidence():
    cit = Citation(
        source_id="vat-art-28b",
        article_ref="art. 28b ust. 1",
        quoted_span="tego zdania nie ma w zrodle",
    )
    node = make_verify_grounding_node()
    update = node({"classification": _classification([cit]), "legal_context": [_CHUNK]})
    out = update["classification"]
    assert out.grounding_status == GroundingStatus.UNSUPPORTED
    assert out.confidence <= CONFIDENCE_CAP_UNSUPPORTED
    assert any("niepotwierdzone" in m for m in out.human_must_confirm)


def test_no_citations_marks_unsupported():
    node = make_verify_grounding_node()
    update = node({"classification": _classification([]), "legal_context": [_CHUNK]})
    assert update["classification"].grounding_status == GroundingStatus.UNSUPPORTED


def test_weak_abstention_is_passed_through_untouched():
    weak = _classification([], status=GroundingStatus.WEAK, confidence=0.4)
    node = make_verify_grounding_node()
    update = node({"classification": weak, "legal_context": []})
    assert update["classification"].grounding_status == GroundingStatus.WEAK
    assert update["classification"].confidence == 0.4


def test_citation_with_unknown_source_id_is_unsupported():
    cit = Citation(
        source_id="nieistniejacy", article_ref="art. 999", quoted_span="Miejscem swiadczenia uslug"
    )
    node = make_verify_grounding_node()
    update = node({"classification": _classification([cit]), "legal_context": [_CHUNK]})
    assert update["classification"].grounding_status == GroundingStatus.UNSUPPORTED
