from pathlib import Path

from invoicer.models import Invoice, TaxTreatment
from invoicer.rag.eval import build_invoice_from_case, load_cases

_CASES = Path(__file__).resolve().parents[2] / "data" / "evals" / "legal_cases.jsonl"


def test_dataset_loads_and_has_expected_fields():
    cases = load_cases(_CASES)
    assert len(cases) >= 5
    for case in cases:
        assert case["expected_treatment"] in {t.value for t in TaxTreatment}
        assert isinstance(case["expected_article_refs"], list)
        assert case["seller_country"]


def test_build_invoice_from_case_produces_valid_invoice():
    case = load_cases(_CASES)[0]
    inv = build_invoice_from_case(case)
    assert isinstance(inv, Invoice)
    assert inv.seller.country == case["seller_country"]


def test_dataset_covers_key_treatments():
    treatments = {c["expected_treatment"] for c in load_cases(_CASES)}
    # rdzen kontrastu podatkowego musi byc reprezentowany
    assert {"import_uslug", "wnt", "import_towarow"} <= treatments
