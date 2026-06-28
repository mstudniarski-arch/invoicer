from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from invoicer.app import AppSettings, create_app, preflight_env

_FULL_ENV = {
    "ANTHROPIC_API_KEY": "sk-test",
    "GMAIL_SENDER_FILTER": "owner@example.com",
    "TWILIO_ACCOUNT_SID": "ACxxxxxxxx",
    "TWILIO_AUTH_TOKEN": "tok",
    "TWILIO_WHATSAPP_FROM": "whatsapp:+1",
    "APPROVER_WHATSAPP_TO": "whatsapp:+2",
}


def _settings(tmp_path) -> AppSettings:
    return AppSettings(
        approver_phone="whatsapp:+48111",
        gmail_sender="owner@example.com",
        data_dir=tmp_path,
        # tryb testowy: bez realnych adapterow / scheduler nie startuje
        test_mode=True,
    )


def test_health_returns_200(tmp_path):
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_status_returns_llm_and_pipeline(tmp_path):
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        r = client.get("/status")
        assert r.status_code == 200
        body = r.json()
        assert "llm" in body and "pipeline" in body
        assert body["pipeline"]["pending"] == 0
        assert body["pipeline"]["processed"] == 0
        assert body["pipeline"]["failed"] == 0


def test_inbound_returns_no_pending_for_unknown_phone(tmp_path):
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        r = client.post(
            "/whatsapp/inbound",
            data={"From": "whatsapp:+48999", "Body": "TAK"},
        )
        assert r.status_code == 200
        assert r.json() == {"status": "no_pending"}


def test_inbound_ignored_for_unknown_body(tmp_path):
    app = create_app(settings=_settings(tmp_path))
    with TestClient(app) as client:
        r = client.post(
            "/whatsapp/inbound",
            data={"From": "whatsapp:+48111", "Body": "?"},
        )
        assert r.json() == {"status": "ignored"}


def test_preflight_passes_with_full_required_env():
    preflight_env(_FULL_ENV)  # komplet sekretow -> brak wyjatku


def test_preflight_reports_all_missing_core_secrets():
    with pytest.raises(RuntimeError) as exc:
        preflight_env({})
    msg = str(exc.value)
    for key in (
        "ANTHROPIC_API_KEY",
        "GMAIL_SENDER_FILTER",
        "TWILIO_AUTH_TOKEN",
        "APPROVER_WHATSAPP_TO",
    ):
        assert key in msg


def test_preflight_requires_fakturownia_creds_when_sink_is_fakturownia():
    env = dict(_FULL_ENV, INVOICER_SINK="fakturownia")
    with pytest.raises(RuntimeError) as exc:
        preflight_env(env)
    assert "FAKTUROWNIA_API_TOKEN" in str(exc.value)


def test_preflight_requires_voyage_key_when_database_url_set():
    env = dict(_FULL_ENV, DATABASE_URL="postgresql://x")
    with pytest.raises(RuntimeError) as exc:
        preflight_env(env)
    assert "VOYAGE_API_KEY" in str(exc.value)


def test_sentry_not_initialized_in_test_mode(tmp_path, monkeypatch):
    import invoicer.app as appmod

    calls: list[str | None] = []
    monkeypatch.setattr(appmod, "init_sentry", lambda dsn: calls.append(dsn) or False)
    app = create_app(settings=_settings(tmp_path))
    # test_mode: Sentry NIE jest inicjalizowany (brak realnych adapterow/sekretow)
    assert calls == []
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
