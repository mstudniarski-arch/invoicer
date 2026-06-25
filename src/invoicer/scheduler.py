from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from invoicer.observability_status import PipelineCounters
from invoicer.ports import EmailSource, InvoiceDetector
from invoicer.processed import ProcessedDocuments, document_key
from invoicer.runner import fetch_invoice_documents, request_invoice_approval

_logger = logging.getLogger("invoicer.scheduler")


def run_intake(
    graph: Any,
    channel: Any,
    registry: Any,
    source: EmailSource,
    detector: InvoiceDetector,
    *,
    sender: str,
    phone: str,
    counters: PipelineCounters,
    processed: ProcessedDocuments,
    request_fn: Callable[..., dict | None] = request_invoice_approval,
    alert: Callable[[str, str], None] = lambda *_: None,
) -> None:
    """Reaktywny zaciag (polling): Gmail -> detekcja -> per-faktura request akceptacji.

    Idempotentny: pomija dokumenty juz obsluzone (ProcessedDocuments) — ten sam mail
    nie generuje powtornych prosb co cykl. At-most-once: blad faktury -> mark 'failed'
    + alert 'manualna interwencja' (BEZ ponawiania). thread_id generowany lokalnie.
    """
    import uuid

    try:
        docs = fetch_invoice_documents(source, detector, sender)
    except Exception as exc:  # noqa: BLE001 - blad zaciagu (np. wygasly token Gmaila) musi zaalarmowac
        _logger.exception("intake: pobranie faktur nie powiodlo sie")
        alert("intake", str(exc))
        raise
    _logger.info("intake start: %d faktur (przed dedup)", len(docs))
    for doc in docs:
        key = document_key(doc)
        if processed.seen(key):
            continue
        thread_id = f"intake-{uuid.uuid4()}"
        try:
            request_fn(graph, channel, registry, doc, thread_id=thread_id, phone=phone)
            processed.mark(key, "done")
            counters.incr_processed()
        except Exception as exc:  # noqa: BLE001 - at-most-once: zapisz failed, NIE ponawiaj, zaalarmuj
            processed.mark(key, "failed")
            counters.incr_failed()
            _logger.exception("intake: faktura %s nie przeszla (manualna interwencja)", doc.filename)
            alert(doc.filename, f"wymaga manualnej interwencji: {exc}")
    _logger.info("intake done: processed=%d failed=%d", counters.processed, counters.failed)


def build_scheduler(job: Callable[[], None], *, interval_minutes: int) -> AsyncIOScheduler:
    """Buduje AsyncIOScheduler z jednym interwalowym jobem; coalesce + max_instances=1.

    coalesce + max_instances=1: gdy przebieg przeciagnie sie ponad interwal,
    kolejny tick jest pominiety (nie nakladaja sie rownolegle).
    """
    sched = AsyncIOScheduler()
    sched.add_job(
        job,
        IntervalTrigger(minutes=interval_minutes),
        id="intake",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    return sched
