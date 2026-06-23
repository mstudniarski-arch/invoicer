from __future__ import annotations

from invoicer.models import InvoiceDocument


class StubInvoiceDetector:
    """Testowy/offline InvoiceDetector: zwraca z gory ustalona odpowiedz (domyslnie True)."""

    def __init__(self, *, result: bool = True) -> None:
        self._result = result

    def is_invoice(self, document: InvoiceDocument) -> bool:
        return self._result
