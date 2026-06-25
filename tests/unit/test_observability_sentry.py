from __future__ import annotations

from invoicer.observability_sentry import _scrub, init_sentry


def test_init_sentry_noop_without_dsn():
    assert init_sentry(None) is False
    assert init_sentry("") is False


def test_scrub_redacts_pii_in_nested_event():
    event = {
        "logentry": {"message": "blad faktury NIP 5260001246"},
        "exception": {"values": [{"value": "kontakt ksiegowa@firma.pl"}]},
        "extra": {"iban": "PL61109010140000071219812874"},
        "level": "error",
    }
    out = _scrub(event, None)
    flat = str(out)
    assert "5260001246" not in flat
    assert "ksiegowa@firma.pl" not in flat
    assert "PL61109010140000071219812874" not in flat
    assert "[NIP]" in flat and "[EMAIL]" in flat and "[KONTO]" in flat
    assert out["level"] == "error"  # nie-stringi nietkniete


def test_init_sentry_calls_sdk_with_scrub(monkeypatch):
    import sentry_sdk

    captured = {}
    monkeypatch.setattr(sentry_sdk, "init", lambda **kw: captured.update(kw))
    assert init_sentry("https://abc@o1.ingest.sentry.io/1") is True
    assert captured["before_send"] is _scrub
    assert captured["send_default_pii"] is False
    # before_breadcrumb to druga bramka PII (breadcrumbs z LoggingIntegration) — musi redagowac
    crumb = captured["before_breadcrumb"]({"message": "NIP 5260001246"}, None)
    assert "5260001246" not in str(crumb)
    assert "[NIP]" in str(crumb)
