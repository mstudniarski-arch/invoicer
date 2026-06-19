from __future__ import annotations

from pydantic import BaseModel, Field

from invoicer.models import Classification, CountryBucket, TaxTreatment


class ClassificationJudgment(BaseModel):
    """DTO wypelniane przez sedziego-LLM (with_structured_output). Bez country_bucket —
    ten pozostaje z deterministycznego classify (kraj jest pewny, nie zgadujemy go)."""

    treatment: TaxTreatment = Field(
        description="Traktowanie: import_uslug | import_towarow | wnt | inne (dla zagranicznej)"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Pewnosc osadu 0..1")
    rationale_pl: str = Field(description="Krotkie uzasadnienie po polsku")
    human_must_confirm: list[str] = Field(
        default_factory=list, description="Co czlowiek musi potwierdzic"
    )
    currency_note: str = Field(default="", description="Nota walutowa, jesli waluta != PLN")


def judgment_to_classification(
    judgment: ClassificationJudgment, country_bucket: CountryBucket
) -> Classification:
    """Laczy osad LLM z pewnym (deterministycznym) country_bucket w domenowy Classification."""
    return Classification(
        treatment=judgment.treatment,
        country_bucket=country_bucket,
        confidence=judgment.confidence,
        rationale_pl=judgment.rationale_pl,
        human_must_confirm=judgment.human_must_confirm,
        currency_note=judgment.currency_note,
    )
