from __future__ import annotations

import logging

from invoicer.booking import BookingPayload, BookingResult

logger = logging.getLogger("invoicer.mock_subiekt")


class MockSubiektSink:
    """AccountingSink udajacy Subiekt: loguje dekret i zwraca deterministyczny wynik.

    Realny zapis do Subiekt GT wymaga Windows + Sfera (COM) — patrz spec, SubiektSferaSink.
    """

    sink_name = "mock-subiekt"

    def post(self, payload: BookingPayload) -> BookingResult:
        booking_id = f"MOCK-{payload.number}"
        logger.info(
            "Zaksiegowano (mock): numer=%s sprzedawca=%s brutto=%s waluta=%s traktowanie=%s",
            payload.number,
            payload.seller.name,
            payload.total_gross,
            payload.currency,
            payload.treatment,
        )
        return BookingResult(booking_id=booking_id, sink=self.sink_name)
