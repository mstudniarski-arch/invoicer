from __future__ import annotations

import sys
import tempfile
import uuid
from pathlib import Path

import streamlit as st

# `streamlit run` nie dziedziczy PYTHONPATH=src (pakiet celowo nieinstalowany), wiec dokladamy
# katalog src/ do sys.path, by `import invoicer` dzialal niezaleznie od sposobu startu.
_SRC = str(Path(__file__).resolve().parents[2])
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from invoicer.runner import (  # noqa: E402
    build_demo_graph,
    document_from_upload,
    resume_document,
    start_document,
)
from invoicer.security import install_redaction  # noqa: E402

install_redaction()  # scrubuje PII ze wszystkich logow invoicera (idempotentne)

st.set_page_config(page_title="Invoicer — demo", page_icon="🧾")
st.title("🧾 Invoicer — agentic invoice intake")
st.caption(
    "Wgraj fakturę PDF → ekstrakcja (Claude) → walidacja PL"
    " → klasyfikacja → akceptacja człowieka → księgowanie."
)

if "graph" not in st.session_state:
    ledger_path = Path(tempfile.mkdtemp(prefix="invoicer_demo_")) / "ledger.jsonl"
    st.session_state.graph = build_demo_graph(ledger_path=ledger_path)
    st.session_state.payload = None
    st.session_state.result = None
    st.session_state.thread_id = None

uploaded = st.file_uploader("Faktura (PDF)", type=["pdf"])

if st.button("Przetwórz", disabled=uploaded is None):
    doc = document_from_upload(uploaded.name, uploaded.getvalue())
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.payload = start_document(
        st.session_state.graph, doc, thread_id=st.session_state.thread_id
    )
    st.session_state.result = None

payload = st.session_state.payload
if payload and st.session_state.result is None:
    st.subheader("Do akceptacji")
    cols = st.columns(2)
    cols[0].metric("Numer", payload["number"])
    cols[1].metric("Brutto", f"{payload['total_gross']} {payload['currency']}")
    st.write(f"**Sprzedawca:** {payload['seller']} ({payload['country']})")
    st.write(f"**Traktowanie:** `{payload['treatment']}` — {payload['rationale']}")
    if payload["flags"]:
        st.warning("Flagi: " + ", ".join(payload["flags"]))
    if payload["must_confirm"]:
        st.info("Do potwierdzenia: " + "; ".join(payload["must_confirm"]))
    decision = st.columns(2)
    if decision[0].button("✅ Zatwierdź"):
        st.session_state.result = resume_document(
            st.session_state.graph, thread_id=st.session_state.thread_id, decision="approve"
        )
    if decision[1].button("❌ Odrzuć"):
        st.session_state.result = resume_document(
            st.session_state.graph, thread_id=st.session_state.thread_id, decision="reject"
        )

result = st.session_state.result
if result is not None:
    booking = result.get("booking")
    if booking is not None:
        st.success(f"Zaksięgowano (mock): {booking.booking_id} → {booking.sink}")
    else:
        st.error("Odrzucono — nic nie zaksięgowano.")
