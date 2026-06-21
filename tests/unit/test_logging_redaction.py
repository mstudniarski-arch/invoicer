import logging
from decimal import Decimal

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.booking import BookingPayload
from invoicer.models import Party
from invoicer.security import RedactingFilter, install_redaction


def _capturing_handler() -> tuple[logging.Handler, list[str]]:
    """Handler zbierajacy sformatowane linie do listy (deterministyczny, bez I/O)."""
    lines: list[str] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(self.format(record))

    handler = _ListHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler, lines


def _payload_with_pii() -> BookingPayload:
    # nazwa sprzedawcy zawiera e-mail (realny przypadek: kontakt z naglowka faktury)
    return BookingPayload(
        seller=Party(name="ACME ksiegowa@firma.pl", nip="5260001246", country="PL"),
        buyer=Party(name="Klient", country="PL"),
        number="FV/1",
        currency="PLN",
        lines=[],
        total_net=Decimal("1000.00"),
        total_vat=Decimal("230.00"),
        total_gross=Decimal("1230.00"),
        treatment="krajowa",
    )


def test_redacting_filter_masks_pii_from_record_args():
    f = RedactingFilter()
    record = logging.LogRecord(
        name="invoicer.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="sprzedawca=%s nip=%s",
        args=("ACME ksiegowa@klient.pl", "5260001246"),
        exc_info=None,
    )
    assert f.filter(record) is True
    out = record.getMessage()
    assert "ksiegowa@klient.pl" not in out
    assert "5260001246" not in out
    assert "[EMAIL]" in out
    assert "[NIP]" in out


def test_install_redaction_is_idempotent():
    logger = logging.getLogger("invoicer.test_idem")
    handler, _ = _capturing_handler()
    logger.addHandler(handler)
    try:
        install_redaction(logger)
        install_redaction(logger)
        n = sum(isinstance(flt, RedactingFilter) for flt in handler.filters)
        assert n == 1
    finally:
        logger.removeHandler(handler)


def test_install_redaction_adds_handler_when_none():
    logger = logging.getLogger("invoicer.test_nohandler")
    logger.handlers.clear()
    try:
        install_redaction(logger)
        assert logger.handlers  # dodano handler
        assert any(isinstance(flt, RedactingFilter) for h in logger.handlers for flt in h.filters)
    finally:
        logger.handlers.clear()


def test_install_redaction_redacts_child_logger_output():
    parent = logging.getLogger("invoicer.test_parent")
    handler, lines = _capturing_handler()
    parent.addHandler(handler)
    parent.setLevel(logging.INFO)
    old_propagate = parent.propagate
    parent.propagate = False  # izolacja: tylko nasz handler
    install_redaction(parent)
    try:
        child = logging.getLogger("invoicer.test_parent.child")
        child.info("nip=%s", "5260001246")  # propaguje do handlera parenta
        assert lines == ["nip=[NIP]"]
    finally:
        parent.removeHandler(handler)
        parent.propagate = old_propagate


def test_mock_subiekt_log_is_redacted_after_install():
    parent = logging.getLogger("invoicer")
    handler, lines = _capturing_handler()
    parent.addHandler(handler)
    parent.setLevel(logging.INFO)
    old_propagate = parent.propagate
    parent.propagate = False
    install_redaction(parent)
    try:
        MockSubiektSink().post(_payload_with_pii())
        joined = "\n".join(lines)
        assert "ksiegowa@firma.pl" not in joined
        assert "[EMAIL]" in joined
    finally:
        parent.removeHandler(handler)
        parent.propagate = old_propagate


def test_filter_does_not_raise_on_bad_format_args():
    f = RedactingFilter()
    # zly log: format oczekuje 2 argumentow, podano 1 -> getMessage() rzuca TypeError
    record = logging.LogRecord(
        name="invoicer.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="a=%s b=%s",
        args=("tylko_jeden",),
        exc_info=None,
    )
    # filtr NIE moze rzucic (logowanie nie moze wywalic aplikacji)
    assert f.filter(record) is True


def test_multiple_redacting_filters_on_same_record_are_safe():
    f = RedactingFilter()
    record = logging.LogRecord(
        name="invoicer.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="nip=%s",
        args=("5260001246",),
        exc_info=None,
    )
    f.filter(record)
    first = record.getMessage()
    f.filter(record)  # symuluje filtr drugiego handlera na tym samym rekordzie
    assert record.getMessage() == first  # idempotentne, brak podwojnego manglowania
    assert "[NIP]" in first
