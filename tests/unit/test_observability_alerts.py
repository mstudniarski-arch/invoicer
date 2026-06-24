from __future__ import annotations

from invoicer.observability_alerts import format_failure_alert, send_failure_alert


class _FakeChannel:
    def __init__(self, *, boom: bool = False):
        self.sent: list[str] = []
        self._boom = boom

    def notify(self, text: str) -> None:
        if self._boom:
            raise RuntimeError("twilio down")
        self.sent.append(text)


def test_format_failure_alert():
    msg = format_failure_alert("faktura.pdf", "ekstrakcja padla")
    assert msg.startswith("⚠️")
    assert "faktura.pdf" in msg
    assert "ekstrakcja padla" in msg


def test_send_failure_alert_delivers():
    ch = _FakeChannel()
    send_failure_alert(ch, "⚠️ test")
    assert ch.sent == ["⚠️ test"]


def test_send_failure_alert_never_raises_when_channel_fails():
    ch = _FakeChannel(boom=True)
    # alert nie moze wywalic pipeline'u — blad kanalu jest polykany
    send_failure_alert(ch, "⚠️ test")  # nie rzuca
