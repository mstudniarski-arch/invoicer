from invoicer.adapters.twilio_whatsapp import TwilioError, TwilioWhatsAppChannel


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


def _channel(http: _FakeHttp) -> TwilioWhatsAppChannel:
    return TwilioWhatsAppChannel(
        http,
        account_sid="ACx",
        auth_token="tok",
        from_whatsapp="whatsapp:+1415",
        to_whatsapp="whatsapp:+48999",
    )


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
