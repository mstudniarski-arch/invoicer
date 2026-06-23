from fastapi.testclient import TestClient

from invoicer.webhook import create_inbound_app, parse_decision


class _FakeRegistry:
    def __init__(self, mapping):
        self._mapping = dict(mapping)
        self.resolved: list[str] = []

    def resolve_oldest(self, phone):
        self.resolved.append(phone)
        return self._mapping.get(phone)


def _client(registry, resumes):
    def _resume(graph, *, thread_id, decision):
        resumes.append((thread_id, decision))

    app = create_inbound_app(graph=object(), registry=registry, resume=_resume)
    return TestClient(app)


def test_parse_decision_variants():
    assert parse_decision("TAK") == "approve"
    assert parse_decision(" yes ") == "approve"
    assert parse_decision("1") == "approve"
    assert parse_decision("NIE") == "reject"
    assert parse_decision("2") == "reject"
    assert parse_decision("co?") is None
    assert parse_decision("") is None
    assert parse_decision("   ") is None


def test_inbound_approve_resumes_oldest_thread():
    reg = _FakeRegistry({"whatsapp:+48500": "t1"})
    resumes: list = []
    resp = _client(reg, resumes).post(
        "/whatsapp/inbound", data={"From": "whatsapp:+48500", "Body": "TAK"}
    )
    assert resp.json()["status"] == "resumed"
    assert resumes == [("t1", "approve")]


def test_inbound_reject_resumes_with_reject():
    reg = _FakeRegistry({"whatsapp:+48500": "t1"})
    resumes: list = []
    resp = _client(reg, resumes).post(
        "/whatsapp/inbound", data={"From": "whatsapp:+48500", "Body": "nie"}
    )
    assert resp.json()["status"] == "resumed"
    assert resumes == [("t1", "reject")]


def test_inbound_unrecognized_does_not_resume():
    reg = _FakeRegistry({"whatsapp:+48500": "t1"})
    resumes: list = []
    resp = _client(reg, resumes).post(
        "/whatsapp/inbound", data={"From": "whatsapp:+48500", "Body": "moze"}
    )
    assert resp.json()["status"] == "ignored"
    assert resumes == []


def test_inbound_no_pending_does_not_resume():
    reg = _FakeRegistry({})
    resumes: list = []
    resp = _client(reg, resumes).post(
        "/whatsapp/inbound", data={"From": "whatsapp:+999", "Body": "TAK"}
    )
    assert resp.json()["status"] == "no_pending"
    assert resumes == []


def test_inbound_resume_failure_returns_resume_failed():
    reg = _FakeRegistry({"whatsapp:+48500": "t1"})

    def _boom(graph, *, thread_id, decision):
        raise RuntimeError("stale checkpoint dla NIP 5260001246")

    app = create_inbound_app(graph=object(), registry=reg, resume=_boom)
    resp = TestClient(app).post(
        "/whatsapp/inbound", data={"From": "whatsapp:+48500", "Body": "TAK"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "resume_failed"
    assert resp.json()["thread_id"] == "t1"
    # PII z wyjatku nie moze wyciec do odpowiedzi HTTP
    assert "5260001246" not in resp.text
