import json

from invoicer.adapters.fixture_source import FixtureSource
from invoicer.ports import EmailSource


def _write_fixture(directory, name, sender, content=b"%PDF-1.4 x"):
    (directory / f"{name}.pdf").write_bytes(content)
    (directory / f"{name}.json").write_text(
        json.dumps({"sender": sender, "subject": "Faktura", "received_at": "2026-06-01T10:00:00"}),
        encoding="utf-8",
    )


def test_fixture_source_satisfies_email_source_protocol(tmp_path):
    assert isinstance(FixtureSource(tmp_path), EmailSource)


def test_fetch_filters_by_sender(tmp_path):
    _write_fixture(tmp_path, "a", "ksiegowa@klient.pl")
    _write_fixture(tmp_path, "b", "ktos@inny.pl")
    docs = FixtureSource(tmp_path).fetch("ksiegowa@klient.pl")
    assert len(docs) == 1
    assert docs[0].filename == "a.pdf"
    assert docs[0].sender == "ksiegowa@klient.pl"
    assert docs[0].content.startswith(b"%PDF")


def test_fetch_returns_empty_for_unknown_sender(tmp_path):
    _write_fixture(tmp_path, "a", "ksiegowa@klient.pl")
    assert FixtureSource(tmp_path).fetch("nieznany@x.pl") == []
