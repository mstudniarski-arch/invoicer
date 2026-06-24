from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from invoicer.observability_status import PipelineCounters
from invoicer.ports import EmailSource, InvoiceDetector
from invoicer.runner import fetch_invoice_documents, request_invoice_approval

_logger = logging.getLogger("invoicer.scheduler")


def run_daily_intake(
    graph: Any,
    channel: Any,
    registry: Any,
    source: EmailSource,
    detector: InvoiceDetector,
    *,
    sender: str,
    phone: str,
    counters: PipelineCounters,
    request_fn: Callable[..., dict | None] = request_invoice_approval,
) -> None:
    """Codzienny zaciag: Gmail -> detekcja -> per-faktura request akceptacji.

    Per-faktura try/except: jedna zla faktura nie blokuje pozostalych.
    `request_fn` wstrzykiwany (testy/CI: stub). thread_id generowany lokalnie.
    """
    import uuid

    docs = fetch_invoice_documents(source, detector, sender)
    _logger.info("intake start: %d faktur do przetworzenia", len(docs))
    for doc in docs:
        thread_id = f"intake-{uuid.uuid4()}"
        try:
            request_fn(graph, channel, registry, doc, thread_id=thread_id, phone=phone)
            counters.incr_processed()
        except Exception:
            counters.incr_failed()
            # nie podnosimy — kolejna faktura ma sie przetworzyc; szczegoly w Sentry/log (Plan 2)
            _logger.exception("intake: faktura %s nie przeszla", doc.filename)
    _logger.info(
        "intake done: processed=%d failed=%d", counters.processed, counters.failed
    )


def build_scheduler(
    job: Callable[[], None], *, hour: int, minute: int, tz: str
) -> AsyncIOScheduler:
    """Buduje AsyncIOScheduler z jednym cron-jobem; coalesce + max_instances=1."""
    sched = AsyncIOScheduler(timezone=tz)
    sched.add_job(
        job,
        CronTrigger(hour=hour, minute=minute, timezone=tz),
        id="daily-intake",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    return sched
