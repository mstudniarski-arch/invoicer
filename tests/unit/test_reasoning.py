import pytest
from pydantic import ValidationError

from invoicer.models import Classification, CountryBucket, TaxTreatment
from invoicer.reasoning import ClassificationJudgment, judgment_to_classification


def test_classification_confidence_now_bounded():
    with pytest.raises(ValidationError):
        Classification(
            treatment=TaxTreatment.KRAJOWA, country_bucket=CountryBucket.PL, confidence=1.5
        )


def test_judgment_maps_to_classification_keeping_bucket():
    j = ClassificationJudgment(
        treatment=TaxTreatment.IMPORT_USLUG,
        confidence=0.8,
        rationale_pl="UK, usluga zdalna -> import uslug.",
        human_must_confirm=["stawka 23%"],
        currency_note="GBP -> NBP",
    )
    c = judgment_to_classification(j, CountryBucket.POZA_UE)
    assert c.treatment == TaxTreatment.IMPORT_USLUG
    assert c.country_bucket == CountryBucket.POZA_UE  # bucket z deterministycznego classify
    assert c.confidence == 0.8
    assert c.human_must_confirm == ["stawka 23%"]


def test_judgment_confidence_bounded():
    with pytest.raises(ValidationError):
        ClassificationJudgment(
            treatment=TaxTreatment.INNE, confidence=2.0, rationale_pl="x", human_must_confirm=[]
        )
