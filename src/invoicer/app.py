from __future__ import annotations

import logging
import os
from collections.abc import Mapping
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
from invoicer.observability import LlmMetrics, LlmMetricsCallback
from invoicer.observability_alerts import format_failure_alert, send_failure_alert
from invoicer.observability_langsmith import init_langsmith
from invoicer.observability_sentry import init_sentry
from invoicer.observability_status import PipelineCounters, pipeline_status
from invoicer.processed import ProcessedDocuments
from invoicer.runner import (
    _demo_invoice,
    active_sink_name,
    build_legal_store,
    build_sink,
    persistent_checkpointer,
)
from invoicer.scheduler import build_scheduler, run_intake
from invoicer.security import install_redaction
from invoicer.webhook import create_inbound_app


@dataclass
class AppSettings:
    approver_phone: str
    gmail_sender: str
    intake_interval_minutes: int = 5
    gmail_lookback_days: int = 3
    data_dir: Path = Path("/data")
    test_mode: bool = False  # True w testach: stuby + scheduler nie startuje


_REQUIRED_CORE_ENV = (
    "ANTHROPIC_API_KEY",  # extractor / reasoner / detector
    "GMAIL_SENDER_FILTER",  # zaciag z Gmaila
    "TWILIO_ACCOUNT_SID",  # kanal akceptacji WhatsApp
    "TWILIO_AUTH_TOKEN",
    "TWILIO_WHATSAPP_FROM",
    "APPROVER_WHATSAPP_TO",
)


def preflight_env(env: Mapping[str, str]) -> None:
    """Fail-fast walidacja konfiguracji na starcie: lepiej paść czytelnie przy boocie niz

    cicho dopiero przy pierwszej fakturze. Sprawdza komplet sekretow rdzennych oraz
    zaleznosci warunkowe (Fakturownia gdy INVOICER_SINK=fakturownia; VOYAGE_API_KEY gdy
    ustawiony DATABASE_URL — inaczej pgvector/RAG byloby cicho wylaczone). GMAIL_TOKEN_B64
    nie jest wymagane: token moze juz lezec na wolumenie (/data/token.json).
    """
    missing = [k for k in _REQUIRED_CORE_ENV if not env.get(k)]
    if env.get("INVOICER_SINK", "").lower() == "fakturownia":
        missing += [k for k in ("FAKTUROWNIA_API_TOKEN", "FAKTUROWNIA_DOMAIN") if not env.get(k)]
    if env.get("DATABASE_URL") and not env.get("VOYAGE_API_KEY"):
        missing.append("VOYAGE_API_KEY")
    if missing:
        raise RuntimeError(
            "Brak/niepelna konfiguracja srodowiska: "
            + ", ".join(missing)
            + " — ustaw przez `fly secrets set ...` przed startem."
        )


def _settings_from_env() -> AppSettings:
    return AppSettings(
        approver_phone=os.environ["APPROVER_WHATSAPP_TO"],
        gmail_sender=os.environ["GMAIL_SENDER_FILTER"],
        intake_interval_minutes=int(os.getenv("INTAKE_INTERVAL_MINUTES", "5")),
        gmail_lookback_days=int(os.getenv("GMAIL_LOOKBACK_DAYS", "3")),
        data_dir=Path(os.getenv("INVOICER_DATA_DIR", "/data")),
    )


_METRICS_MODEL = "claude-sonnet-4-6"  # model adapterow Claude (pricing dla LlmMetricsCallback)


def _real_claude_adapters(metrics: LlmMetrics):
    """(extractor, reasoner) Claude z LlmMetricsCallback — by /status mial realny koszt."""
    from invoicer.adapters.claude_extractor import ClaudeVisionExtractor
    from invoicer.adapters.claude_reasoner import ClaudeExceptionReasoner

    cb = LlmMetricsCallback(metrics, model=_METRICS_MODEL)
    return ClaudeVisionExtractor(callbacks=[cb]), ClaudeExceptionReasoner(callbacks=[cb])


def _build_real_graph(settings: AppSettings, checkpointer, metrics: LlmMetrics):
    """Realne adaptery: Claude (z metrykami) + Fakturownia/MockSubiekt + ledger na wolumenie."""
    extractor, reasoner = _real_claude_adapters(metrics)

    def _mark_email_read(message_id: str) -> None:
        # Po zaksiegowaniu: oznacz zrodlowy mail jako przeczytany. Swiezy serwis z tokenu
        # (booking jest rzadki, bramkowany czlowiekiem). Best-effort — book node lapie bledy.
        from invoicer.adapters.gmail import GmailAdapter
        from invoicer.adapters.gmail_auth import gmail_service_from_token

        service = gmail_service_from_token(settings.data_dir / "token.json")
        GmailAdapter(service).mark_read(message_id)

    return build_invoice_graph(
        extractor=extractor,
        reasoner=reasoner,
        ledger=Ledger(settings.data_dir / "ledger.jsonl"),
        sink=build_sink(),
        checkpointer=checkpointer,
        store=build_legal_store(),
        mark_read=_mark_email_read,
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
    if settings is None:  # produkcja (uvicorn factory): walidacja env zanim cokolwiek zbudujemy
        preflight_env(os.environ)
        settings = _settings_from_env()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    install_redaction()  # root: redaguje takze uvicorn/httpx/third-party, nie tylko invoicer.*

    channel = None
    on_resume_failure = None
    if not settings.test_mode:
        init_sentry(os.getenv("SENTRY_DSN"))
        init_langsmith()  # tracing per-faktura w LangSmith gdy ustawiony LANGSMITH_API_KEY
        bootstrap_gmail_token("GMAIL_TOKEN_B64", settings.data_dir / "token.json")
        from invoicer.adapters.twilio_whatsapp import build_twilio_whatsapp_channel

        channel = build_twilio_whatsapp_channel()

        def on_resume_failure(thread_id: str, exc: Exception) -> None:
            send_failure_alert(channel, format_failure_alert(f"watek {thread_id}", str(exc)))

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(settings.data_dir / "invoicer_state.sqlite")
    checkpointer = persistent_checkpointer(db_path)
    registry = PendingApprovals(db_path)
    processed = ProcessedDocuments(db_path)
    metrics = LlmMetrics()
    counters = PipelineCounters()

    graph = (
        _build_test_graph(settings, checkpointer)
        if settings.test_mode
        else _build_real_graph(settings, checkpointer, metrics)
    )

    # webhook (reuzycie logiki Planu B) + dodatkowe endpointy.
    # Walidacja podpisu Twilio aktywna w prod gdy ustawiony WEBHOOK_PUBLIC_URL (+ token);
    # w test_mode wylaczona, by testy nie musialy podpisywac zadan.
    # link_secret: podpis linkow tap-to-approve (/approve,/reject); fallback na TWILIO_AUTH_TOKEN.
    link_secret = None
    if not settings.test_mode:
        link_secret = os.getenv("APPROVAL_LINK_SECRET") or os.getenv("TWILIO_AUTH_TOKEN")
    app = create_inbound_app(
        graph,
        registry,
        on_resume_failure=on_resume_failure,
        twilio_auth_token=None if settings.test_mode else os.getenv("TWILIO_AUTH_TOKEN"),
        public_url=None if settings.test_mode else os.getenv("WEBHOOK_PUBLIC_URL"),
        link_secret=link_secret,
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/status")
    def status() -> dict:
        return pipeline_status(
            metrics, counters, registry, phone=settings.approver_phone, sink=active_sink_name()
        )

    if settings.test_mode:
        return app

    @asynccontextmanager
    async def _lifespan(_: FastAPI):
        from invoicer.adapters.claude_detector import ClaudeInvoiceDetector
        from invoicer.adapters.gmail import GmailAdapter
        from invoicer.adapters.gmail_auth import gmail_service_from_token

        def _job() -> None:
            service = gmail_service_from_token(settings.data_dir / "token.json")
            run_intake(
                graph,
                channel,
                registry,
                GmailAdapter(service, lookback_days=settings.gmail_lookback_days),
                ClaudeInvoiceDetector(
                    callbacks=[LlmMetricsCallback(metrics, model=_METRICS_MODEL)]
                ),
                sender=settings.gmail_sender,
                phone=settings.approver_phone,
                counters=counters,
                processed=processed,
                alert=lambda ctx, reason: send_failure_alert(
                    channel, format_failure_alert(ctx, reason)
                ),
            )

        scheduler = build_scheduler(
            _job,
            interval_minutes=settings.intake_interval_minutes,
        )
        scheduler.start()
        try:
            yield
        finally:
            scheduler.shutdown(wait=False)

    app.router.lifespan_context = _lifespan
    return app


# Uvicorn startuje przez FACTORY: `uvicorn invoicer.app:_factory --factory` (Dockerfile CMD).
# Ten modul-level `app` jest celowo None (placeholder) — NIE jest sciezka uruchomienia.
app: FastAPI | None = None


def _factory() -> FastAPI:  # uvicorn factory mode (patrz Dockerfile CMD)
    return create_app()
