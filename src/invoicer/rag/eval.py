from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from invoicer.models import Invoice, LineItem, Party


def recall_at_k(retrieved_refs: list[str], expected_refs: set[str], k: int) -> float:
    """Ulamek oczekiwanych article_ref obecnych w top-k zwroconych. Pusty expected -> 1.0."""
    if not expected_refs:
        return 1.0
    top = set(retrieved_refs[:k])
    return len(top & expected_refs) / len(expected_refs)


def reciprocal_rank(retrieved_refs: list[str], expected_refs: set[str]) -> float:
    """1/pozycja pierwszego trafionego oczekiwanego ref (1-indexed); 0.0 gdy brak."""
    for position, ref in enumerate(retrieved_refs, start=1):
        if ref in expected_refs:
            return 1.0 / position
    return 0.0


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def load_cases(path: Path) -> list[dict]:
    """Wczytuje golden dataset (JSONL) z przypadkami ewaluacyjnymi."""
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def build_invoice_from_case(case: dict) -> Invoice:
    """Buduje minimalna Invoice z przypadku eval (do query/klasyfikacji). Kwoty umowne."""
    net = Decimal("1000.00")
    descriptions = case["line_descriptions"]
    per_line = (net / len(descriptions)).quantize(Decimal("0.01"))
    lines = [
        LineItem(
            description=desc,
            quantity=Decimal("1"),
            unit_net=per_line,
            vat_rate=Decimal("0.00") if case.get("no_vat") else Decimal("0.23"),
            net=per_line,
            vat=Decimal("0.00") if case.get("no_vat") else (per_line * Decimal("0.23")),
            gross=per_line if case.get("no_vat") else (per_line * Decimal("1.23")),
        )
        for desc in descriptions
    ]
    total_net = sum((ln.net for ln in lines), Decimal("0"))
    total_vat = sum((ln.vat for ln in lines), Decimal("0"))
    return Invoice(
        seller=Party(name="Eval Seller", country=case["seller_country"]),
        buyer=Party(name="Eval Buyer", nip="5260001246", country="PL"),
        number=f"EVAL/{case['id']}",
        issue_date=date(2026, 1, 1),
        currency=case["currency"],
        lines=lines,
        total_net=total_net,
        total_vat=total_vat,
        total_gross=total_net + total_vat,
    )
