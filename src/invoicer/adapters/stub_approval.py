from __future__ import annotations


class StubApprovalChannel:
    """Testowy ApprovalChannel: rejestruje wyslane payloady i thread_id (CI/offline)."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.thread_ids: list[str | None] = []

    def request_approval(self, payload: dict, *, thread_id: str | None = None) -> None:
        self.sent.append(payload)
        self.thread_ids.append(thread_id)
