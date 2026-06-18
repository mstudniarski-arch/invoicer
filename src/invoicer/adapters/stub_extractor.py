from __future__ import annotations

from invoicer.models import Invoice, InvoiceDocument


class StubExtractor:
    """Deterministyczny InvoiceExtractor do testow/demo offline.

    Zwraca z gory ustalona Invoice (niezalezna kopie), bez kontaktu z LLM.
    Realny ClaudeVisionExtractor dochodzi w Planie 04.
    """

    def __init__(self, invoice: Invoice) -> None:
        self._invoice = invoice

    def extract(self, document: InvoiceDocument) -> Invoice:
        return self._invoice.model_copy(deep=True)
