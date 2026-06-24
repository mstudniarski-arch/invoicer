from __future__ import annotations

from fastapi.testclient import TestClient

from invoicer.app import AppSettings, create_app


def _settings(tmp_path) -> AppSettings:
    return AppSettings(
        approver_phone="whatsapp:+48111",
        gmail_sender="owner@example.com",
        intake_hour=8,
        intake_minute=0,
        intake_tz="Europe/Warsaw",
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
