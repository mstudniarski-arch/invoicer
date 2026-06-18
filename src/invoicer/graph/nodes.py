from __future__ import annotations

from invoicer.ledger import Ledger
from invoicer.ports import InvoiceExtractor
from invoicer.state import InvoiceState
from invoicer.validation import validate_invoice

LOW_CONFIDENCE = 0.6


def make_extract_node(extractor: InvoiceExtractor):
    """Wezel `extract`: surowy dokument -> Invoice (przez wstrzykniety ekstraktor)."""

    def extract(state: InvoiceState) -> dict:
        # Licznik 'absolutny': czytaj biezacy + 1 i zwroc wartosc. InvoiceState.extract_attempts
        # celowo NIE ma reducera (domyslny LastValue/nadpisanie) — przy ewentualnej petli retry
        # odczyt-inkrementacja-nadpisanie daje poprawna kumulacje. operator.add zepsuloby to.
        attempts = state.get("extract_attempts", 0) + 1
        invoice = extractor.extract(state["document"])
        update: dict = {"invoice": invoice, "extract_attempts": attempts}
        conf = invoice.extraction_confidence
        if conf is not None and conf < LOW_CONFIDENCE:
            update["errors"] = [f"Niska pewnosc ekstrakcji: {conf:.2f}"]
        return update

    return extract


def make_validate_node(ledger: Ledger):
    """Wezel `validate`: deterministyczna walidacja + wykrywanie duplikatow (ledger)."""

    def validate(state: InvoiceState) -> dict:
        return {"validation": validate_invoice(state["invoice"], ledger=ledger)}

    return validate
