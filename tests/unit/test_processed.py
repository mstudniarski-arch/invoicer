from __future__ import annotations

import hashlib
from datetime import datetime

from invoicer.models import InvoiceDocument
from invoicer.processed import ProcessedDocuments, document_key


def _doc(*, message_id=None, filename="f.pdf", content=b"%PDF") -> InvoiceDocument:
    return InvoiceDocument(
        sender="a@b.pl",
        received_at=datetime(2026, 6, 25),
        filename=filename,
        content=content,
        message_id=message_id,
    )


def test_seen_false_then_true_after_mark_done(tmp_path):
    store = ProcessedDocuments(str(tmp_path / "s.sqlite"))
    assert store.seen("k1") is False
    store.mark("k1", "done")
    assert store.seen("k1") is True


def test_seen_true_after_mark_failed(tmp_path):
    store = ProcessedDocuments(str(tmp_path / "s.sqlite"))
    store.mark("k2", "failed")
    assert store.seen("k2") is True  # failed tez liczy sie jako 'obsluzony' (at-most-once)


def test_mark_is_idempotent(tmp_path):
    store = ProcessedDocuments(str(tmp_path / "s.sqlite"))
    store.mark("k", "failed")
    store.mark("k", "done")  # INSERT OR REPLACE — bez bledu
    assert store.seen("k") is True


def test_persists_across_instances(tmp_path):
    path = str(tmp_path / "s.sqlite")
    ProcessedDocuments(path).mark("k", "done")
    assert ProcessedDocuments(path).seen("k") is True  # trwale (nowe polaczenie widzi wpis)


def test_document_key_uses_message_id_and_filename():
    a = document_key(_doc(message_id="m1", filename="a.pdf"))
    b = document_key(_doc(message_id="m1", filename="b.pdf"))
    assert a == "m1:a.pdf"
    assert a != b  # jeden mail, dwa zalaczniki = dwa rozne klucze


def test_document_key_falls_back_to_content_hash_without_message_id():
    k = document_key(_doc(message_id=None, filename="x.pdf", content=b"DANE"))
    assert k == f"{hashlib.sha256(b'DANE').hexdigest()}:x.pdf"
