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


def test_compute_twilio_signature_matches_documented_vector():
    # Wektor referencyjny z dokumentacji Twilio (Security / validating signatures) —
    # dowodzi, ze nasz HMAC-SHA1 + base64 liczy DOKLADNIE to, czego oczekuje Twilio.
    from invoicer.webhook import compute_twilio_signature

    url = "https://mycompany.com/myapp.php?foo=1&bar=2"
    params = {
        "CallSid": "CA1234567890ABCDE",
        "Caller": "+14158675309",
        "Digits": "1234",
        "From": "+14158675309",
        "To": "+18005551212",
    }
    # Wartosc potwierdzona niezaleznym oraclem: openssl dgst -sha1 -hmac 12345 | base64.
    assert compute_twilio_signature("12345", url, params) == "RSOYDt4T1cUTdK1PDd93/VVr8B8="


def test_inbound_rejects_request_without_valid_signature():
    # Walidacja wlaczona (token + public_url) -> brak/zly podpis = 403, ZADNEGO resume.
    reg = _FakeRegistry({"whatsapp:+48500": "t1"})
    resumes: list = []

    def _resume(graph, *, thread_id, decision):
        resumes.append((thread_id, decision))

    app = create_inbound_app(
        graph=object(),
        registry=reg,
        resume=_resume,
        twilio_auth_token="secret",
        public_url="https://app.fly.dev/whatsapp/inbound",
    )
    resp = TestClient(app).post(
        "/whatsapp/inbound", data={"From": "whatsapp:+48500", "Body": "TAK"}
    )
    assert resp.status_code == 403
    assert resumes == []


def test_inbound_accepts_valid_signature_and_resumes():
    from invoicer.webhook import compute_twilio_signature

    reg = _FakeRegistry({"whatsapp:+48500": "t1"})
    resumes: list = []

    def _resume(graph, *, thread_id, decision):
        resumes.append((thread_id, decision))

    url = "https://app.fly.dev/whatsapp/inbound"
    app = create_inbound_app(
        graph=object(),
        registry=reg,
        resume=_resume,
        twilio_auth_token="secret",
        public_url=url,
    )
    params = {"From": "whatsapp:+48500", "Body": "TAK"}
    sig = compute_twilio_signature("secret", url, params)
    resp = TestClient(app).post(
        "/whatsapp/inbound", data=params, headers={"X-Twilio-Signature": sig}
    )
    assert resp.json()["status"] == "resumed"
    assert resumes == [("t1", "approve")]


def test_inbound_calls_on_resume_failure_and_returns_2xx():
    from fastapi.testclient import TestClient

    from invoicer.webhook import create_inbound_app

    class _Registry:
        def resolve_oldest(self, phone):
            return "thread-1"

    def boom_resume(graph, *, thread_id, decision):
        raise RuntimeError("ksiegowanie padlo")

    captured: list[tuple[str, str]] = []

    app = create_inbound_app(
        object(),
        _Registry(),
        resume=boom_resume,
        on_resume_failure=lambda thread_id, exc: captured.append((thread_id, str(exc))),
    )
    client = TestClient(app)
    r = client.post("/whatsapp/inbound", data={"From": "whatsapp:+48111", "Body": "TAK"})
    assert r.status_code == 200
    assert r.json()["status"] == "resume_failed"
    assert captured == [("thread-1", "ksiegowanie padlo")]
