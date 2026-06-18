from invoicer.models import Classification, CountryBucket, TaxTreatment


def test_treatment_and_bucket_are_string_enums():
    assert TaxTreatment.IMPORT_USLUG == "import_uslug"
    assert CountryBucket.POZA_UE == "poza_UE"


def test_classification_defaults():
    c = Classification(treatment=TaxTreatment.KRAJOWA, country_bucket=CountryBucket.PL)
    assert c.confidence == 1.0
    assert c.rationale_pl == ""
    assert c.human_must_confirm == []
    assert c.currency_note == ""


def test_classification_full():
    c = Classification(
        treatment=TaxTreatment.IMPORT_USLUG,
        country_bucket=CountryBucket.POZA_UE,
        confidence=0.7,
        rationale_pl="UK bez VAT",
        human_must_confirm=["usluga czy towar?"],
        currency_note="GBP -> NBP",
    )
    assert c.treatment == TaxTreatment.IMPORT_USLUG
    assert c.human_must_confirm == ["usluga czy towar?"]
