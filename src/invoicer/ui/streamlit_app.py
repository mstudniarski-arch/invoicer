from __future__ import annotations

import html
import os
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

st.set_page_config(page_title="INVOICER // intake", page_icon="🛰️", layout="centered")

_CSS = (Path(__file__).resolve().parent / "cyberpunk.css").read_text(encoding="utf-8")
st.markdown(f"<style>{_CSS}</style>", unsafe_allow_html=True)

_esc = html.escape

_BADGE = {
    "idle": ("IDLE", "idle"),
    "done": ("DONE", "done"),
    "ok": ("OK", "ok"),
    "active": ("WAIT", "active"),
    "skip": ("SKIP", "skip"),
    "warn": ("FLAG", "warn"),
    "fail": ("STOP", "fail"),
}


def _card(num: str, name: str, status: str, detail: str) -> str:
    label, cls = _BADGE.get(status, ("…", "idle"))
    return (
        f'<div class="cp-step cp-{cls}"><div class="cp-rail">'
        f'<span class="cp-num">{num}</span></div><div class="cp-card">'
        f'<div class="cp-head"><span class="cp-name">{_esc(name)}</span>'
        f'<span class="cp-badge cp-b-{cls}">{label}</span></div>'
        f'<div class="cp-detail">{detail}</div></div></div>'
    )


def _chip(check) -> str:
    s = str(check.status)
    icon = {"pass": "✓", "warn": "!", "fail": "✕"}.get(s, "·")
    return f'<span class="cp-chip cp-chip-{s}">{_esc(check.name)} {icon}</span>'


def _foreign(cls) -> bool:
    return bool(cls) and str(cls.country_bucket) != "PL"


def _step_extract(inv) -> str:
    if not inv:
        return _card("02", "extract · Claude vision", "idle", "—")
    conf = inv.extraction_confidence
    tail = "" if conf is None else f" · pewnosc {conf:.0%}"
    detail = (
        f"{_esc(inv.number)} · {_esc(inv.seller.name)} ({_esc(inv.seller.country)}) · "
        f"{inv.total_gross} {_esc(inv.currency)}{tail}"
    )
    return _card("02", "extract · Claude vision", "done", detail)


def _step_classify(cls) -> str:
    if not cls:
        return _card("04", "classify · routing podatkowy", "idle", "—")
    kind = f"zagraniczna ({cls.country_bucket})" if _foreign(cls) else "krajowa (PL)"
    return _card(
        "04", "classify · routing podatkowy", "done", f"{kind} → <b>{_esc(str(cls.treatment))}</b>"
    )


def _step_retrieve(cls, ctx) -> str:
    if cls and not _foreign(cls):
        return _card("05", "retrieve_legal_context", "skip", "krajowa — pomijane")
    if _foreign(cls) and ctx is not None:
        if ctx:
            arts = ", ".join(_esc(c.article_ref) for c in ctx)
            return _card(
                "05", "retrieve_legal_context · pgvector", "done", f"{len(ctx)} przepis(y): {arts}"
            )
        return _card(
            "05",
            "retrieve_legal_context · pgvector",
            "warn",
            "brak trafnych przepisow → abstention",
        )
    return _card("05", "retrieve_legal_context", "idle", "—")


def _step_reason(cls) -> str:
    if cls and not _foreign(cls):
        return _card("06", "reason_exception · grounded", "skip", "krajowa — pomijane")
    if _foreign(cls) and cls:
        cites = ", ".join(_esc(c.article_ref) for c in cls.citations) or "—"
        return _card(
            "06",
            "reason_exception · grounded",
            "done",
            f"cytaty: <b>{cites}</b> · {_esc(cls.rationale_pl[:110])}",
        )
    return _card("06", "reason_exception · grounded", "idle", "—")


def _step_verify(cls) -> str:
    if cls and not _foreign(cls):
        return _card("07", "verify_grounding", "skip", "krajowa — pomijane")
    if _foreign(cls) and cls:
        gs = str(cls.grounding_status)
        status = {"grounded": "ok", "weak": "warn", "unsupported": "warn"}.get(gs, "done")
        return _card(
            "07",
            "verify_grounding · faithfulness",
            status,
            f"grounding: <b>{gs}</b> · pewnosc {cls.confidence:.0%}",
        )
    return _card("07", "verify_grounding", "idle", "—")


def _pipeline(state, payload, result) -> str:
    inv = state.get("invoice") if state else None
    val = state.get("validation") if state else None
    cls = state.get("classification") if state else None
    ctx = state.get("legal_context") if state else None
    booked = result.get("booking") if result else None
    waiting = payload is not None and result is None
    cards = [
        _card(
            "01",
            "fetch / upload",
            "done" if state else "idle",
            _esc(state["document"].filename) if state else "oczekuje na PDF",
        ),
        _step_extract(inv),
        _card(
            "03",
            "validate · NIP / sumy / duplikat",
            ("warn" if val.hard_errors else "ok") if val else "idle",
            " ".join(_chip(c) for c in val.checks) if val else "—",
        ),
        _step_classify(cls),
        _step_retrieve(cls, ctx),
        _step_reason(cls),
        _step_verify(cls),
        _card(
            "08",
            "human_review · BRAMKA CZLOWIEKA",
            "active" if waiting else ("done" if result is not None else "idle"),
            "czeka na Twoja decyzje ↓"
            if waiting
            else ("decyzja podjeta" if result is not None else "—"),
        ),
    ]
    if booked:
        cards.append(
            _card(
                "09",
                "book · księgowanie + ledger",
                "ok",
                f"{_esc(booked.booking_id)} → {_esc(booked.sink)}",
            )
        )
    elif result is not None:
        cards.append(
            _card("09", "book · księgowanie + ledger", "fail", "odrzucono — nic nie zaksiegowano")
        )
    else:
        cards.append(_card("09", "book · księgowanie + ledger", "idle", "—"))
    return '<div class="cp-pipe">' + "".join(cards) + "</div>"


def _graph_state():
    tid = st.session_state.get("thread_id")
    if not tid:
        return None
    try:
        return st.session_state.graph.get_state({"configurable": {"thread_id": tid}}).values
    except Exception:
        return None


# ---- header ----
st.markdown(
    '<div class="cp-title">🛰 INVOICER // agentic invoice intake</div>', unsafe_allow_html=True
)
st.markdown(
    '<div class="cp-sub">PDF → Claude vision → walidacja PL → klasyfikacja → '
    "legal-grounded RAG → <b>BRAMKA CZLOWIEKA</b> → ksiegowanie</div>",
    unsafe_allow_html=True,
)

if "graph" not in st.session_state:
    ledger_path = Path(tempfile.mkdtemp(prefix="invoicer_demo_")) / "ledger.jsonl"
    st.session_state.graph = build_demo_graph(ledger_path=ledger_path)
    st.session_state.payload = None
    st.session_state.result = None
    st.session_state.thread_id = None

uploaded = st.file_uploader("Faktura (PDF)", type=["pdf"])
if st.button("▶ PRZETWÓRZ", disabled=uploaded is None, type="primary"):
    doc = document_from_upload(uploaded.name, uploaded.getvalue())
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.payload = start_document(
        st.session_state.graph, doc, thread_id=st.session_state.thread_id
    )
    st.session_state.result = None

state = _graph_state()
payload = st.session_state.payload
result = st.session_state.result

# ---- live pipeline ----
st.markdown(_pipeline(state, payload, result), unsafe_allow_html=True)

# ---- human gate ----
if payload and result is None:
    st.markdown(
        '<div class="cp-gate-title">// WYMAGANA DECYZJA CZLOWIEKA</div>', unsafe_allow_html=True
    )
    if payload.get("citations"):
        basis = ", ".join(_esc(c) for c in payload["citations"])
        gs = _esc(str(payload.get("grounding_status", "—")))
        st.markdown(
            f'<div class="cp-gate-line">podstawa prawna: <b>{basis}</b> · '
            f"grounding: <b>{gs}</b></div>",
            unsafe_allow_html=True,
        )
    if payload.get("flags"):
        st.warning("Flagi walidacji: " + ", ".join(payload["flags"]))
    if payload.get("must_confirm"):
        st.info("Do potwierdzenia przez czlowieka:\n\n- " + "\n- ".join(payload["must_confirm"]))
    cols = st.columns(2)
    if cols[0].button("✅ ZATWIERDŹ → KSIĘGUJ", type="primary"):
        st.session_state.result = resume_document(
            st.session_state.graph, thread_id=st.session_state.thread_id, decision="approve"
        )
        st.rerun()
    if cols[1].button("✕ ODRZUĆ"):
        st.session_state.result = resume_document(
            st.session_state.graph, thread_id=st.session_state.thread_id, decision="reject"
        )
        st.rerun()

# ---- final banner ----
if result is not None:
    booking = result.get("booking")
    if booking is not None:
        if booking.sink == "fakturownia":
            domain = os.getenv("FAKTUROWNIA_DOMAIN", "")
            link = (
                f" — [otwórz w Fakturowni](https://{domain}.fakturownia.pl/invoices?income=no)"
                if domain
                else ""
            )
            st.success(f"✅ Zaksięgowano w Fakturowni (koszt): {booking.booking_id}{link}")
        else:
            st.success(f"✅ Zaksięgowano [{booking.sink}]: {booking.booking_id}")
    else:
        st.error("✕ Odrzucono — nic nie zaksięgowano (bramka człowieka zadziałała).")
