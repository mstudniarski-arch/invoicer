from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from invoicer.adapters.mock_subiekt import MockSubiektSink
from invoicer.adapters.stub_extractor import StubExtractor
from invoicer.adapters.stub_reasoner import IdentityReasoner
from invoicer.approvals import PendingApprovals
from invoicer.bootstrap import bootstrap_gmail_token
from invoicer.graph.build import build_invoice_graph
from invoicer.ledger import Ledger
from invoicer.observability import LlmMetrics
from invoicer.observability_alerts import format_failure_alert, send_failure_alert
from invoicer.observability_sentry import init_sentry
from invoicer.observability_status import PipelineCounters, pipeline_status
from invoicer.runner import _demo_invoice, persistent_checkpointer
from invoicer.scheduler import build_scheduler, run_daily_intake
from invoicer.security import install_redaction
from invoicer.webhook import create_inbound_app


@dataclass
class AppSettings:
    approver_phone: str
    gmail_sender: str
    intake_hour: int = 8
    intake_minute: int = 0
    intake_tz: str = "Europe/Warsaw"
    data_dir: Path = Path("/data")
    test_mode: bool = False  # True w testach: stuby + scheduler nie startuje


def _settings_from_env() -> AppSettings:
    return AppSettings(
        approver_phone=os.environ["APPROVER_WHATSAPP_TO"],
        gmail_sender=os.environ["GMAIL_SENDER_FILTER"],
        intake_hour=int(os.getenv("INTAKE_HOUR", "8")),
        intake_minute=int(os.getenv("INTAKE_MINUTE", "0")),
        intake_tz=os.getenv("INTAKE_TZ", "Europe/Warsaw"),
        data_dir=Path(os.getenv("INVOICER_DATA_DIR", "/data")),
    )


def _build_real_graph(settings: AppSettings, checkpointer):
    """Realne adaptery: Claude + Fakturownia (lub MockSubiekt) + ledger na wolumenie."""
    from invoicer.adapters.claude_extractor import ClaudeVisionExtractor
    from invoicer.adapters.claude_reasoner import ClaudeExceptionReasoner

    if os.getenv("INVOICER_SINK", "").lower() == "fakturownia":
        from invoicer.adapters.fakturownia import build_fakturownia_sink

        sink = build_fakturownia_sink()
    else:
        sink = MockSubiektSink()
    return build_invoice_graph(
        extractor=ClaudeVisionExtractor(),
        reasoner=ClaudeExceptionReasoner(),
        ledger=Ledger(settings.data_dir / "ledger.jsonl"),
        sink=sink,
        checkpointer=checkpointer,
    )


def _build_test_graph(settings: AppSettings, checkpointer):
    return build_invoice_graph(
        extractor=StubExtractor(_demo_invoice()),
        reasoner=IdentityReasoner(),
        ledger=Ledger(settings.data_dir / "ledger.jsonl"),
        sink=MockSubiektSink(),
        checkpointer=checkpointer,
    )


def create_app(*, settings: AppSettings | None = None) -> FastAPI:
    """Fabryka aplikacji: durable graf + registry + webhook + /health + /status + scheduler."""
    settings = settings or _settings_from_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    install_redaction(logging.getLogger("invoicer"))

    channel = None
    on_resume_failure = None
    if not settings.test_mode:
        init_sentry(os.getenv("SENTRY_DSN"))
        bootstrap_gmail_token("GMAIL_TOKEN_B64", settings.data_dir / "token.json")
        from invoicer.adapters.twilio_whatsapp import build_twilio_whatsapp_channel

        channel = build_twilio_whatsapp_channel()

        def on_resume_failure(thread_id: str, exc: Exception) -> None:
            send_failure_alert(channel, format_failure_alert(f"watek {thread_id}", str(exc)))

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(settings.data_dir / "invoicer_state.sqlite")
    checkpointer = persistent_checkpointer(db_path)
    registry = PendingApprovals(db_path)
    metrics = LlmMetrics()
    counters = PipelineCounters()

    graph = (
        _build_test_graph(settings, checkpointer)
        if settings.test_mode
        else _build_real_graph(settings, checkpointer)
    )

    # webhook (reuzycie logiki Planu B) + dodatkowe endpointy
    app = create_inbound_app(graph, registry, on_resume_failure=on_resume_failure)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/status")
    def status() -> dict:
        return pipeline_status(metrics, counters, registry, phone=settings.approver_phone)

    if settings.test_mode:
        return app

    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        from invoicer.adapters.claude_detector import ClaudeInvoiceDetector
        from invoicer.adapters.gmail import GmailAdapter
        from invoicer.adapters.gmail_auth import gmail_service_from_token

        def _job() -> None:
            service = gmail_service_from_token(settings.data_dir / "token.json")
            run_daily_intake(
                graph,
                channel,
                registry,
                GmailAdapter(service),
                ClaudeInvoiceDetector(),
                sender=settings.gmail_sender,
                phone=settings.approver_phone,
                counters=counters,
                alert=lambda ctx, reason: send_failure_alert(
                    channel, format_failure_alert(ctx, reason)
                ),
            )

        scheduler = build_scheduler(
            _job,
            hour=settings.intake_hour,
            minute=settings.intake_minute,
            tz=settings.intake_tz,
        )
        scheduler.start()
        try:
            yield
        finally:
            scheduler.shutdown(wait=False)

    app.router.lifespan_context = _lifespan
    return app


# Eksponowane dla uvicorn (invoicer.app:app).
# Tworzone leniwie wewnatrz, gdy uvicorn faktycznie laduje modul w kontenerze.
app: FastAPI | None = None


def _factory() -> FastAPI:  # uvicorn factory mode
    return create_app()
