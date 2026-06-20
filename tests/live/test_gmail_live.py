import os
from pathlib import Path

import pytest

from invoicer.adapters.gmail import GmailAdapter
from invoicer.adapters.gmail_auth import gmail_service_from_token

_TOKEN = Path(os.getenv("GMAIL_TOKEN", "token.json"))
_SENDER = os.getenv("GMAIL_SENDER_FILTER", "")

pytestmark = pytest.mark.skipif(
    not _TOKEN.exists() or not _SENDER,
    reason="wymaga token.json (po authorize_gmail) + GMAIL_SENDER_FILTER (test live)",
)


def test_real_gmail_fetch_returns_documents():
    service = gmail_service_from_token(_TOKEN)
    docs = GmailAdapter(service).fetch(_SENDER)
    assert isinstance(docs, list)
    for doc in docs:
        assert doc.filename.lower().endswith(".pdf")
        assert doc.content[:4] == b"%PDF"
