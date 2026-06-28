from invoicer.adapters.twilio_whatsapp import (
    TwilioError,
    TwilioUndelivered,
    TwilioWhatsAppChannel,
)

_PAYLOAD = {
    "number": "FV/1",
    "seller": "ACME",
    "seller_nip": "5260001246",
    "total_gross": "1230.00",
    "currency": "PLN",
    "treatment": "krajowa",
}


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class _FakeHttp:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[tuple] = []

    def post(self, url, *, data, auth):
        self.calls.append((url, data, auth))
        return self._response


class _SeqHttp:
    """post() -> jedna odpowiedz; get() -> kolejne statusy z listy (poll dostarczenia)."""

    def __init__(self, post_resp: _FakeResponse, get_resps: list[_FakeResponse]) -> None:
        self._post = post_resp
        self._gets = list(get_resps)
        self.posts: list[tuple] = []
        self.gets: list[str] = []

    def post(self, url, *, data, auth):
        self.posts.append((url, data, auth))
        return self._post

    def get(self, url, *, auth):
        self.gets.append(url)
        return self._gets.pop(0)


def _channel(http: _FakeHttp) -> TwilioWhatsAppChannel:
    return TwilioWhatsAppChannel(
        http,
        account_sid="ACx",
        auth_token="tok",
        from_whatsapp="whatsapp:+1415",
        to_whatsapp="whatsapp:+48999",
    )


def test_format_message_includes_tap_links_when_provided():
    from invoicer.adapters.twilio_whatsapp import format_approval_message

    links = {
        "approve": "https://app.fly.dev/approve/t1?t=aa",
        "reject": "https://app.fly.dev/reject/t1?t=bb",
    }
    msg = format_approval_message(_PAYLOAD, links=links)
    assert "https://app.fly.dev/approve/t1?t=aa" in msg
    assert "https://app.fly.dev/reject/t1?t=bb" in msg
    assert "Zatwierdz" in msg


def test_format_message_falls_back_to_tak_nie_without_links():
    from invoicer.adapters.twilio_whatsapp import format_approval_message

    msg = format_approval_message(_PAYLOAD)
    assert "TAK" in msg and "NIE" in msg


def test_request_approval_body_carries_signed_tap_links():
    from invoicer.approval_links import sign_decision

    http = _FakeHttp(_FakeResponse(201, text='{"sid":"SM1"}'))
    channel = TwilioWhatsAppChannel(
        http,
        account_sid="ACx",
        auth_token="tok",
        from_whatsapp="whatsapp:+1415",
        to_whatsapp="whatsapp:+48999",
        base_url="https://app.fly.dev",
        link_secret="K",
    )
    channel.request_approval(_PAYLOAD, thread_id="t-1", confirm_delivery=False)
    _, data, _ = http.calls[0]
    tok = sign_decision("K", "t-1", "approve")
    assert f"https://app.fly.dev/approve/t-1?t={tok}" in data["Body"]


def test_notify_posts_message_to_twilio():
    http = _FakeHttp(_FakeResponse(201))
    _channel(http).notify("⚠️ Faktura FV/1: ekstrakcja padla")
    url, data, auth = http.calls[0]
    assert url == "https://api.twilio.com/2010-04-01/Accounts/ACx/Messages.json"
    assert data == {
        "From": "whatsapp:+1415",
        "To": "whatsapp:+48999",
        "Body": "⚠️ Faktura FV/1: ekstrakcja padla",
    }
    assert auth == ("ACx", "tok")


def test_notify_raises_on_non_2xx_with_redacted_body():
    http = _FakeHttp(_FakeResponse(401, text='{"error":"Bad sid AC1234567890"}'))
    try:
        _channel(http).notify("hello")
    except TwilioError as exc:
        msg = str(exc)
        assert "401" in msg
        # sekret nie powinien wyciekac w pelnej formie do wiadomosci wyjatku
        assert "AC1234567890" not in msg
        return
    raise AssertionError("oczekiwano TwilioError")


def test_request_approval_error_does_not_leak_sid():
    # url request_approval zawiera SID — blad NIE moze go wyniesc (logi/Sentry/alert)
    sid = "AC0123456789abcdef0123456789abcdef"
    http = _FakeHttp(_FakeResponse(401, text='{"error":"unauthorized"}'))
    channel = TwilioWhatsAppChannel(
        http,
        account_sid=sid,
        auth_token="tok",
        from_whatsapp="whatsapp:+1415",
        to_whatsapp="whatsapp:+48999",
    )
    payload = {
        "number": "FV/1",
        "seller": "ACME",
        "seller_nip": "5260001246",
        "total_gross": "1230.00",
        "currency": "PLN",
        "treatment": "krajowa",
    }
    try:
        channel.request_approval(payload)
    except TwilioError as exc:
        assert sid not in str(exc)
        return
    raise AssertionError("oczekiwano TwilioError")


def _noop_sleep(_seconds: float) -> None:
    return None


def test_request_approval_raises_when_whatsapp_undelivered():
    # Twilio przyjmuje (201, status=queued), ale WhatsApp NIE dostarcza (63016 = poza 24h oknem).
    # Bramka MUSI to wykryc — inaczej flow falszywie loguje "wyslano" i czeka 15 min na odpowiedz.
    post = _FakeResponse(201, text='{"sid":"SM123","status":"queued","error_code":null}')
    poll = _FakeResponse(200, text='{"sid":"SM123","status":"undelivered","error_code":63016}')
    http = _SeqHttp(post, [poll])
    channel = TwilioWhatsAppChannel(
        http,
        account_sid="ACx",
        auth_token="tok",
        from_whatsapp="whatsapp:+1415",
        to_whatsapp="whatsapp:+48999",
    )
    try:
        channel.request_approval(_PAYLOAD, poll_interval=0.0, sleep=_noop_sleep)
    except TwilioUndelivered as exc:
        assert exc.error_code == 63016
        assert "63016" in str(exc)
        assert http.gets, "powinno odpytac status wiadomosci po wyslaniu"
        return
    raise AssertionError("oczekiwano TwilioUndelivered gdy WhatsApp nie dostarczyl")


def test_request_approval_ok_when_delivered():
    post = _FakeResponse(201, text='{"sid":"SM1","status":"queued","error_code":null}')
    poll = _FakeResponse(200, text='{"sid":"SM1","status":"delivered","error_code":null}')
    http = _SeqHttp(post, [poll])
    channel = TwilioWhatsAppChannel(
        http,
        account_sid="ACx",
        auth_token="tok",
        from_whatsapp="whatsapp:+1415",
        to_whatsapp="whatsapp:+48999",
    )
    channel.request_approval(_PAYLOAD, poll_interval=0.0, sleep=_noop_sleep)  # nie rzuca
    assert http.gets, "powinno potwierdzic dostarczenie"
