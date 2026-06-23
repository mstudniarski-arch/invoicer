from __future__ import annotations


class StubApprovalChannel:
    """Testowy ApprovalChannel: rejestruje wyslane payloady (CI/offline)."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    def request_approval(self, payload: dict) -> None:
        self.sent.append(payload)
