<!-- Wygenerowane i zweryfikowane wieloagentowo (workflow), potem sprawdzone recznie. Towarzyszy scripts/debug_flow.py (narracja runtime). -->

# Invoicer — the definitive inside-out flow guide

This is a line-by-line, execution-ordered walkthrough of one invoice's journey through the LangGraph graph, written for a developer stepping a debugger. Every claim below was re-verified against source. The single most important thing to internalize: **one logical run is split across TWO `graph.invoke` calls (Phase 1 and Phase 2), stitched together only by a shared `thread_id` plus a checkpointer.** Everything else hangs off that fact.

All file paths are relative to the repo root (e.g. `src/invoicer/...`). Line numbers are exact as of the commit that added this doc — re-check if the code moved.

---

## 0. The graph topology (the map you're navigating)

Built in `graph/build.py:48-74`. Edges, in wiring order:

- `add_edge(START, "extract")` — build.py:58
- `add_edge("extract", "validate")` — build.py:59 (unconditional)
- `add_conditional_edges("validate", route_after_validate, {"classify": "classify", "end": END})` — build.py:60-62
- `add_conditional_edges("classify", route_after_classify, {"retrieve_legal_context": "retrieve_legal_context", "human_review": "human_review"})` — build.py:63-67
- `add_edge("retrieve_legal_context", "reason_exception")` — build.py:68
- `add_edge("reason_exception", "verify_grounding")` — build.py:69
- `add_edge("verify_grounding", "human_review")` — build.py:70
- `add_conditional_edges("human_review", route_after_review, {"book": "book", "end": END})` — build.py:71
- `add_edge("book", END)` — build.py:72

So the **full path**:

```
START → extract → validate → ( classify | END )
classify → [ PL: human_review ]  OR  [ foreign: retrieve_legal_context → reason_exception → verify_grounding → human_review ]
human_review → ( book | END )
book → END
```

`builder.compile(checkpointer=checkpointer or InMemorySaver())` — build.py:74. **A checkpointer is mandatory** because `human_review` calls `interrupt()`; with no checkpointer passed, build defaults to `InMemorySaver()` (in-process only). `runner.persistent_checkpointer` (runner.py:207-218) supplies a durable SQLite saver for the cross-process WhatsApp path.

---

## 1. PRE-FLOW: `fetch_invoice_documents` — the "process only invoices" gate

`runner.py:124-132`. Before any graph state exists:

```python
return [doc for doc in source.fetch(sender) if detector.is_invoice(doc)]   # runner.py:132
```

`source.fetch(sender)` (EmailSource port, ports.py:11-14) pulls candidate attachments; the list comprehension keeps only those where `detector.is_invoice(doc)` is True (InvoiceDetector port, ports.py:32-35). Each survivor is fed, one at a time, into a `start_document` call. **No `InvoiceState` exists yet** — this just produces the `list[InvoiceDocument]` that becomes each run's seed `document`.

> Breakpoint: runner.py:132. Inspect which docs pass `is_invoice` before any graph run starts.

---

## 2. `_run_config` — the thread_id factory (the join key for both phases)

`runner.py:62-73`:

```python
return {
    "configurable": {"thread_id": thread_id},   # runner.py:69 — THE checkpointer key
    "run_name": f"invoice-{thread_id}",          # LangSmith only
    "tags": ["invoicer"],
    "metadata": {"thread_id": thread_id},
}
```

`configurable.thread_id` is the checkpointer key. Everything else is LangSmith tracing metadata, ignored when tracing is off. **Both `start_document` (runner.py:78) and `resume_document` (runner.py:86) call `_run_config` with the same `thread_id` string** — that is the only thing tying Phase 1 and Phase 2 together. Pass a different `thread_id` to the resume and LangGraph silently starts a brand-new run from START.

> Breakpoint: runner.py:68 (return). Confirm the SAME `thread_id` string is used by both the start and the resume call.

---

## 3. PHASE 1 — `start_document` → `graph.invoke #1`

`runner.py:76-81`:

```python
config = _run_config(thread_id)                                  # runner.py:78
result = graph.invoke({"document": document, "errors": []}, config)  # runner.py:79  ← invoke #1
interrupts = result.get("__interrupt__")                          # runner.py:80
return interrupts[0].value if interrupts else None                # runner.py:81
```

This seeds `InvoiceState` with `document` and `errors=[]` and drives the graph from START all the way down to `human_review` (or to END if a duplicate short-circuits at `route_after_validate`). `InvoiceState` is `TypedDict, total=False` (state.py:13) so every node may return a partial dict.

The return value path is subtle and important: **the interrupt payload is NOT a normal node return.** When `human_review` calls `interrupt()`, LangGraph returns from `graph.invoke` *normally* with a special key `result["__interrupt__"]` — a list of `Interrupt` objects. `start_document` reads `interrupts[0].value` (the dict the node passed to `interrupt()`) and returns it, or `None` if there was no interrupt.

> Breakpoint: runner.py:79 (watch Phase-1 entry) and runner.py:80 (inspect `result['__interrupt__']` — this confirms the graph paused vs ran to END).

---

## 4. EXTRACT node (START → extract)

The closure from `make_extract_node(extractor)` (nodes.py:28-43), capturing the injected `InvoiceExtractor` (Protocol at ports.py:39-42). Wired at build.py:49.

### 4a. Attempt counter — deliberately NO reducer
nodes.py:32-35:

```python
attempts = state.get("extract_attempts", 0) + 1   # nodes.py:35
```

The comment block (nodes.py:32-34) explains the design: `extract_attempts` is a plain `int` in `InvoiceState` (state.py:22) with **no `Annotated` reducer**, i.e. default LastValue/overwrite. The node does read-current+1-and-overwrite, which accumulates correctly across retries. `operator.add` would double-count. (Verifier nit confirmed: the comment spans nodes.py:32-34, the increment is at nodes.py:35; an earlier citation of "32-37" overshoots by a few lines but the substance is correct.)

### 4b. Polymorphic extract
nodes.py:36: `invoice = extractor.extract(state["document"])`. Which adapter runs is decided at wiring time, not here:

- **Stub path** (offline/CI/demo): `StubExtractor.extract` returns `self._invoice.model_copy(deep=True)` (stub_extractor.py:16-17) — a deep copy of a preset Invoice. **No LLM, and the document is ignored entirely.** Changing the input doc offline will NOT change the extracted invoice. `extraction_confidence` is whatever the fixture set (the `Invoice` default is `None`, models.py:41).
- **Claude path** (real vision): `ClaudeVisionExtractor.extract` (claude_extractor.py:68-72):
  - `build_extraction_message(document)` (claude_extractor.py:35-44) → `_mime_and_block(filename)` (claude_extractor.py:22-32) picks `.pdf → ('application/pdf','file')`, `.png → image/png`, `.jpg/.jpeg → image/jpeg`, **else raises `ValueError`** (claude_extractor.py:30). `EXTRACTION_PROMPT` (claude_extractor.py:13-19) explicitly treats document content as DATA not instructions (prompt-injection guard).
  - `self._client().with_structured_output(InvoiceExtraction).invoke([message])` (claude_extractor.py:70-71). `_client()` lazily builds `ChatAnthropic(model="claude-sonnet-4-6")` only if no llm was injected (claude_extractor.py:61-66; model id at claude_extractor.py:11). CI injects a fake llm.
  - `extraction_to_invoice(extraction)` (extraction.py:78-93): `_amount` parses each money string to `Decimal` (raises `ValueError "Niepoprawna kwota w polu ..."` on bad input, extraction.py:48-52); `_iso_date` parses ISO dates, guarded by truthiness for optional `sale_date`/`due_date` (extraction.py:85-86); the DTO `confidence` (default 1.0, extraction.py:43) maps to `Invoice.extraction_confidence` (extraction.py:92). **These ValueErrors propagate out of the node — they are not caught here.**

### 4c. Confidence gate (a flag, NOT a branch)
nodes.py:37-40:

```python
update: dict = {"invoice": invoice, "extract_attempts": attempts}   # nodes.py:37
conf = invoice.extraction_confidence                                 # nodes.py:38
if conf is not None and conf < LOW_CONFIDENCE:                        # nodes.py:39 (LOW_CONFIDENCE=0.6, nodes.py:22)
    update["errors"] = [f"Niska pewnosc ekstrakcji: {conf:.2f}"]      # nodes.py:40
```

- **None-guard**: a missing confidence (typical for Stub fixtures) never flags.
- **Strict `<`**: `conf == 0.6` is NOT flagged.
- This writes `errors`, which is the **only** accumulating state key (`Annotated[list[str], operator.add]`, state.py:23), so it appends rather than overwrites.
- It does **not** branch the graph; `extract → validate` is unconditional (build.py:59). The string only surfaces later in the `human_review` payload's `flags` (nodes.py:182).

nodes.py:41: `return update`. LangGraph merges: `invoice`/`extract_attempts` overwrite, `errors` appends.

> Breakpoint: nodes.py:39 to watch the `conf==0.6` (NOT flagged) and `conf is None` (skipped) boundaries.

---

## 5. VALIDATE node + route_after_validate (extract → validate)

`make_validate_node(ledger)` closure (nodes.py:69-75): `return {"validation": validate_invoice(state["invoice"], ledger=ledger)}` (nodes.py:73). Pure, deterministic, no LLM.

`validate_invoice` (validation.py:53-113) runs four checks **in this order**:

1. **NIP (FIRST)** — validation.py:63-81. If `seller.country == "PL"`: `nip_checksum_valid(seller.nip)` → PASS or FAIL. Foreign seller → WARN ("NIP PL nie dotyczy"), never FAIL. `nip_checksum_valid` (validation.py:15-29): None/empty → False; strip non-digits; require exactly 10 digits; `weighted = Σ digit[i]*NIP_WEIGHTS[i]` for i in 0..8; `control = weighted % 11`; `control == 10` → False; else `control == int(digits[9])`.
2. **sums (SECOND)** — validation.py:83-92 via `totals_consistent` (validation.py:35-50). Requires ALL FIVE within `_CENT = Decimal("0.01")`: Σnet≈total_net, Σvat≈total_vat, Σgross≈total_gross, `(total_net+total_vat)≈total_gross` globally, AND **every line** `line.net+line.vat≈line.gross` (validation.py:49 — prevents per-line errors canceling in the global sum). Any failure → FAIL.
3. **lines (THIRD)** — validation.py:94-97. Non-empty → PASS; empty → FAIL ("Brak pozycji").
4. **duplicate (FOURTH, only if `ledger is not None`)** — validation.py:99-111. `is_duplicate = ledger.is_duplicate(number, seller.nip, seller.name)`. True → appends a FAIL check AND sets `is_duplicate=True`; False → PASS. **With no ledger, no duplicate check is added and `is_duplicate` stays False.**

`ledger.is_duplicate` (ledger.py:74-76) builds `_dedup_key(number, seller_nip, seller_name) = (number, seller_nip or seller_name)` (ledger.py:26-27) — **NIP preferred, name fallback only when NIP is falsy** — and returns True if any existing entry produces the same key. It re-reads and re-parses the whole JSONL file each call (ledger.py:65-72).

`validate_invoice` returns `ValidationResult(checks, is_duplicate)` (validation.py:113). `ValidationResult` exposes `hard_errors` (FAIL checks), `soft_flags` (WARN checks), and `ok = not hard_errors` (models.py:71-81).

### route_after_validate — branches SOLELY on is_duplicate
nodes.py:78-90:

```python
if state["validation"].is_duplicate:   # nodes.py:85
    ...                                 # log "juz zaksiegowana — pomijam (duplikat)"
    return "end"                        # nodes.py:89 → END
return "classify"                       # nodes.py:90
```

**This is the only early-exit at this stage, and it keys ONLY on `is_duplicate`.** `validation.ok` / `hard_errors` are computed but never read here. A bad NIP, inconsistent sums, or empty lines produce FAIL checks (`ok == False`) but are **not** duplicates, so they still return `"classify"` and reach `human_review` downstream. `ok` is only consumed later, in the `human_review` payload (nodes.py:181).

> Breakpoint: nodes.py:85. Inspect `is_duplicate` AND `hard_errors` together — confirm a non-duplicate FAIL still falls through to `return "classify"` at nodes.py:90.

---

## 6. CLASSIFY node + route_after_classify (validate → classify)

`classify_node` (nodes.py:128-163) is pure and synchronous — **no I/O, no LLM**. If you see latency or external errors "in classify," they are actually upstream (extract) or downstream (retrieve calls the vector store). Reached only for non-duplicates.

The single decision input: `country = invoice.seller.country.upper()` (nodes.py:137). `Party.country` is non-optional, default `"PL"` (models.py:13), so `.upper()` never sees None.

- **DOMESTIC (`country == "PL"`)** — nodes.py:138-143: `Classification(treatment=KRAJOWA, country_bucket=PL, rationale_pl=...)`. `confidence` stays at the model default `1.0` (models.py:117); `human_must_confirm` stays empty.
- **FOREIGN (`country != "PL"`)** — nodes.py:144-162:
  - `bucket = CountryBucket.UE if country in EU_COUNTRIES else CountryBucket.POZA_UE` (nodes.py:145). `EU_COUNTRIES` is a 27-member uppercase frozenset (nodes.py:95-125) that deliberately **includes 'PL'** (nodes.py:117) even though PL can never reach here — documented on purpose at nodes.py:93-94, don't "fix" it.
  - `currency_note` is empty when `currency == "PLN"`, else `"Waluta {currency} — przelicz po kursie NBP."` (nodes.py:146-150). Advisory only.
  - `Classification(treatment=IMPORT_USLUG, country_bucket=bucket, confidence=0.6, ...human_must_confirm=[usluga/towar?, stawka ~23%, kurs NBP], currency_note=...)` (nodes.py:151-162). **`confidence` is explicitly forced to 0.6**, overriding the 1.0 default. `grounding_status` keeps its model default `GROUNDED` (models.py:122) here — it is only downgraded later in reason_exception/verify_grounding. Breakpointing classify expecting WEAK/UNSUPPORTED will mislead you.

nodes.py:163: `return {"classification": classification}` (LastValue overwrite).

### route_after_classify — branches SOLELY on country_bucket
nodes.py:285-289:

```python
if state["classification"].country_bucket == CountryBucket.PL:   # nodes.py:287
    return "human_review"                                         # nodes.py:288
return "retrieve_legal_context"                                   # nodes.py:289
```

**TaxTreatment is intentionally ignored.** PL → `human_review` (skips RAG entirely). UE *or* POZA_UE → `retrieve_legal_context`. **Both foreign buckets take the identical 3-node RAG chain**; the UE/POZA_UE distinction is carried for humans/downstream but does not branch the graph. Unknown/garbage non-PL codes silently fall into POZA_UE + IMPORT_USLUG (no ISO validation here).

> Breakpoint: nodes.py:287. Inspect `country_bucket` — this is THE routing decision; PL bypasses RAG.

---

## 7. FOREIGN branch: retrieve_legal_context → reason_exception → verify_grounding

> **CRITICAL DEFAULT-WIRING NOTE.** `build_invoice_graph` defaults `reasoner = IdentityReasoner()` and `store = InMemoryLegalStore(DeterministicEmbedder())` (build.py:46-47). The store is empty by default, so out of the box this whole arm is **abstention-only**: retrieve returns `[]` → reason_exception takes the WEAK path → verify_grounding short-circuits on WEAK. The GROUNDED/UNSUPPORTED logic is effectively dead code unless a populated store AND a real (non-identity) reasoner are injected.

### 7a. retrieve_legal_context (classify → retrieve_legal_context)
`make_retrieve_legal_context_node(store, k=5, threshold=0.5)` (nodes.py:46-66):

```python
query = build_retrieval_query(state["invoice"])   # nodes.py:61
hits = _search(query)                              # nodes.py:62  (@traceable span, calls store.search(query, k=5) at nodes.py:58)
relevant = [h for h in hits if h.score >= threshold]  # nodes.py:63  (RELEVANCE_THRESHOLD=0.5, nodes.py:23)
return {"legal_context": relevant}                 # nodes.py:64
```

- `build_retrieval_query` (rag/query.py:6-19) is **PII-free / allowlist-only**: seller country, VAT-present flag (`invoice.total_vat > 0`, query.py:15), currency, aggregate net/gross, and line descriptions + net. No buyer PII, no addresses, no party names.
- The 0.5 threshold filter happens **in the node** (nodes.py:63), after the store returns scored top-k. The store does no thresholding.
- `InMemoryLegalStore.search` (in_memory_legal_store.py:48-64): `if not self._rows: return []` (in_memory_legal_store.py:49-50) — the default offline case. Populated → embed query, brute-force cosine vs each stored vector, sort desc, return top-k as `RetrievedChunk` with `score`. `DeterministicEmbedder` is hash-derived (no real semantics), so scores are essentially noise unless query text exactly equals a chunk's text — fine for deterministic tests, not realistic retrieval.

> Breakpoint: nodes.py:63 — inspect raw `hits` (with scores) vs `relevant`. Empty `relevant` decides abstention vs grounded next. Also in_memory_legal_store.py:49 to confirm `self._rows` is empty by default.

### 7b. reason_exception (retrieve_legal_context → reason_exception)
`make_reason_exception_node(reasoner)` (nodes.py:257-282):

```python
base = state["classification"]; context = state.get("legal_context", [])   # nodes.py:265-266
if not context:                                                            # nodes.py:267
    weak = base.model_copy(update={                                        # nodes.py:268-277
        "grounding_status": GroundingStatus.WEAK,
        "confidence": min(base.confidence, CONFIDENCE_CAP_WEAK),           # cap 0.4, nodes.py:24
        "human_must_confirm": [*base.human_must_confirm, "brak wystarczajacej podstawy prawnej..."],
    })
    return {"classification": weak}                                        # nodes.py:278  — NO LLM CALL
enriched = reasoner.reason(state["invoice"], base, context)                # nodes.py:279
return {"classification": enriched}                                        # nodes.py:280
```

- **Empty context → WEAK abstention**, confidence capped to `min(0.6, 0.4) = 0.4`, a human note appended, and the LLM is NOT called.
- Non-empty → `reasoner.reason(...)`. **Default `IdentityReasoner.reason` returns `base` unchanged and ignores context** (stub_reasoner.py:13-16) — so even with context, the default reasoner produces no citations.
- Caps are `min(current, cap)` floors, not assignments.

### 7c. verify_grounding (reason_exception → verify_grounding)
`make_verify_grounding_node()` (nodes.py:300-338):

```python
if classification.grounding_status == GroundingStatus.WEAK:               # nodes.py:309
    return {"classification": classification}                             # nodes.py:310 — passthrough (default offline path)
by_ref = {(c.source_id, c.article_ref): c.text for c in state.get("legal_context", [])}  # nodes.py:311
unsupported = [cit.article_ref for cit in classification.citations
               if not _span_supported(cit.quoted_span,
                                       by_ref.get((cit.source_id, cit.article_ref), ""))]  # nodes.py:312-318
if not classification.citations or unsupported:                          # nodes.py:319
    # → grounding_status=UNSUPPORTED, confidence=min(.,0.3) (cap nodes.py:25), append flag   nodes.py:320-331
else:
    # → grounding_status=GROUNDED                                         nodes.py:332-336
```

- WEAK passes through untouched (abstention already final).
- Match key is the exact `(source_id, article_ref)` tuple (nodes.py:311). A citation whose key doesn't exactly match a retrieved chunk resolves to `""` via `by_ref.get(..., "")` and is treated as unsupported even if the quoted text exists under a different ref.
- `_span_supported` (nodes.py:296-297) is **lenient containment**: `_normalize` collapses whitespace + casefolds both sides (nodes.py:292-293), then a substring `in` test. A paraphrase that happens to be a substring passes — a deterministic stand-in for LLM entailment.
- **Empty citations always → UNSUPPORTED** (nodes.py:319). So a foreign classification that reached verify with no citations becomes UNSUPPORTED, not GROUNDED — the constructor's GROUNDED default is misleading; the LAST writer wins.

Both branches converge on `human_review` (build.py:70).

> Grounding status is set at three points and the last writer wins: constructor GROUNDED (models.py:122) → reason_exception may set WEAK → verify_grounding sets UNSUPPORTED or GROUNDED.

---

## 8. HUMAN_REVIEW — the two-phase interrupt (the heart of the flow)

`human_review` (nodes.py:166-190). This node runs **at the end of Phase 1**, and **again from the top in Phase 2**.

### 8a. Phase-1 pass: build payload, then suspend
nodes.py:171-188 read `invoice`, `validation`, `classification` and assemble the approver-facing payload dict — number, seller, seller_nip, country, total_gross, currency, `validation_ok` (= `validation.ok`, nodes.py:181), `flags` (= hard-error check names + accumulated `state["errors"]`, nodes.py:182), treatment, rationale, `must_confirm`, `grounding_status` (nodes.py:186), `citations` (article_refs, nodes.py:187). For PL invoices, `must_confirm`/`grounding_status`/`citations` are empty/PL defaults because PL skipped the RAG arm.

nodes.py:189:

```python
decision = interrupt(payload)   # nodes.py:189
return {"human_decision": decision}   # nodes.py:190
```

**On the FIRST pass, `interrupt(payload)` raises a `GraphInterrupt` carrying `payload`. Control does NOT reach line 190.** LangGraph catches it, persists the checkpoint under `thread_id`, and `graph.invoke` returns normally with `result["__interrupt__"][0].value == payload`. The run is suspended; nothing past `human_review` has executed. `{"human_decision": decision}` has NOT been written.

The node is **safe to re-execute** because everything before line 189 is pure dict-building — no side effects. If any node placed a side effect before `interrupt()`, it would run twice.

> Breakpoint: nodes.py:189. Step once. In Phase 1, execution STOPS here and unwinds to `graph.invoke`. In Phase 2, it RESUMES here and `interrupt()` returns the decision.

### 8b. The GATE — `cli.process_document` (synchronous bridge)
`cli.py:10-24`:

```python
payload = start_document(graph, document, thread_id=thread_id)   # cli.py:21
if payload is None:                                              # cli.py:22  — no interrupt
    return graph.get_state({"configurable": {"thread_id": thread_id}}).values   # cli.py:23
return resume_document(graph, thread_id=thread_id, decision=decide(payload))    # cli.py:24
```

- `payload is None` is a **normal path**, not an error — it happens when `route_after_validate` sent a duplicate straight to END (the graph never paused). In that case `process_document` returns the final state via `graph.get_state(...).values` without resuming.
- Otherwise it calls `decide(payload)` → a string, and feeds it straight into `resume_document`. `decide` is the sync analog of the WhatsApp webhook.

> Breakpoint: cli.py:22 (None/short-circuit branch) and cli.py:24 (the exact decision string entering Phase 2).

---

## 9. PHASE 2 — `resume_document` → `graph.invoke #2`

`runner.py:84-87`:

```python
config = _run_config(thread_id)                          # runner.py:86 — SAME thread_id
return graph.invoke(Command(resume=decision), config)    # runner.py:87 — invoke #2
```

This is **THE line that proves resume is a second invoke on the same thread, not a fresh run.** LangGraph uses `thread_id` + the checkpointer to reload the suspended checkpoint, re-enters `human_review`, and re-runs its body from the top. This time `interrupt(payload)` at nodes.py:189 **returns** `decision` (the value inside `Command(resume=...)`) instead of raising. The node now reaches nodes.py:190 and writes `{"human_decision": decision}`. Control then hits `route_after_review`.

> Breakpoint: runner.py:87. Inspect `Command.resume` and `config["configurable"]["thread_id"]` — confirm it matches the Phase-1 thread_id. A mismatch is the classic "resume starts a brand-new run from START" bug.

### route_after_review — exact-equality "approve"
nodes.py:193-195:

```python
return "book" if state.get("human_decision") == "approve" else "end"   # nodes.py:195
```

Mapped at build.py:71 to `{"book": "book", "end": END}`. **Only the literal string `"approve"` goes to book.** `"reject"`, `"edit"`, a typo, or `None` all silently route to END with no booking — there is no separate reject handler node. Both `decide()` and the webhook must emit exactly `"approve"` to book.

> Breakpoint: nodes.py:195. Check the returned string against build.py:71's map.

---

## 10. BOOK node (human_review → book, only when approved)

`make_book_node(sink, ledger, clock, mark_read)` (nodes.py:198-254). `clock` defaults to `datetime.now().isoformat(timespec="seconds")` (nodes.py:209). The factory runs at graph-construction time; `book(state, config)` runs per-invoice. Side effects execute in this exact order:

1. **Read invoice** — nodes.py:212.
2. **Duplicate guard (defense-in-depth), BEFORE any write** — nodes.py:213-216: `if ledger.is_duplicate(invoice.number, invoice.seller.nip, invoice.seller.name): raise RuntimeError(...)`. This is a *second* guard; `route_after_validate` (nodes.py:85) already routed known duplicates to END, and `route_after_review` (nodes.py:195) requires approval to reach book. It fires only for state mutated/raced after validate (the ledger is re-read from disk each call, so the two duplicate checks can disagree).
3. **Read classification + thread_id** — nodes.py:217-219: `treatment = str(classification.treatment)`; `thread_id = (config or {}).get("configurable", {}).get("thread_id", "")` — defensive `.get` chain yielding `""` if config/configurable is missing (silent, not an error).
4. **Map to payload** — nodes.py:220 → `invoice_to_booking_payload(invoice, treatment)` (booking.py:33-51). **Deep-copies** seller/buyer/lines (booking.py:40-44) so the dekret is an immutable snapshot; carries `issue_date` and `due_date` (taken straight from the invoice's payment term, **never computed** — booking.py:24/50).
5. **`sink.post(payload)` — the actual booking side effect** — nodes.py:221:
   - `MockSubiektSink.post` (mock_subiekt.py:18-28): logs the dekret, returns `BookingResult(booking_id=f"MOCK-{number}", sink="mock-subiekt")`. Ignores `due_date`.
   - `FakturowniaSink.post` (fakturownia.py:62-105): books a **cost invoice** (`kind="vat"`, `income=0`). **Deliberate seller/buyer SWAP** (fakturownia.py:64-82): because Fakturownia renders `seller_*` under "Nabywca" and `buyer_*` under "Sprzedawca" for cost invoices, our firm (`payload.buyer`) goes into `seller_*` and the PDF supplier (`payload.seller`) into `buyer_*`. Sets `number=payload.number`, issue/sell dates, `reverse_charge = payload.treatment in {"import_uslug"}` (fakturownia.py:85, set `_REVERSE_CHARGE_TREATMENTS` at fakturownia.py:13 — WNT and others deliberately excluded), positions from lines (`total_price_gross` + `tax = int(vat_rate*100)`, fakturownia.py:24-36). Adds `payment_to = due_date.isoformat()` **only if `due_date` present** (fakturownia.py:90-91). POSTs to `https://{domain}.fakturownia.pl/invoices.json`; non-2xx → `FakturowniaError` with PII redacted then truncated to 500 chars (fakturownia.py:95-98) — this raises BEFORE `ledger.append`. `booking_id` = response `number` or `id`.
6. **`ledger.append(LedgerEntry(...))` — the idempotency-critical write** — nodes.py:222-234. Persists number, seller_nip, seller_name, total_gross (as str), booking_id, `booked_at=clock()`, sink, treatment, thread_id. The ledger chains entries by hash (ledger.py:56-63).
7. **Log success** — nodes.py:235-242 (a confirmation marker that post + append both completed).
8. **Best-effort `mark_read`** — nodes.py:243-251: `message_id = document.message_id if document else None` (nodes.py:244, None if no document). Runs only `if mark_read and message_id` (nodes.py:245); wrapped in try/except where **any Exception is swallowed and logged as WARNING** (nodes.py:248-251), so a Gmail failure never unwinds the committed booking. `GmailAdapter.mark_read` does `messages().modify(removeLabelIds=["UNREAD"])`, requiring `gmail.modify` scope. **Order matters: this is AFTER post + append, so it can never block booking.**
9. **Return** — nodes.py:252: `return {"booking": result}`. `book → END` (build.py:72). The Phase-2 `graph.invoke` then returns the final `InvoiceState`.

> Breakpoints: nodes.py:213 (duplicate guard), nodes.py:221 (`sink.post` — for Fakturownia verify the seller/buyer swap and `reverse_charge`), nodes.py:222 (the idempotency-critical `ledger.append`), nodes.py:245 (mark_read guard), nodes.py:252 (final booking dict).

---

## 11. The async (WhatsApp) Phase 2 — same mechanism, different caller

Durability makes the cross-process resume possible. `persistent_checkpointer(db_path)` (runner.py:207-218): `JsonPlusSerializer(allowed_msgpack_modules=_CHECKPOINT_ALLOWED_TYPES)` (runner.py:214; allowlist at runner.py:44-59), `sqlite3.connect(db_path, check_same_thread=False)` (runner.py:215), `SqliteSaver(conn, serde)`, `saver.setup()`. **`check_same_thread=False` is what lets a different thread/process resume the same `thread_id`** long after invoke #1 returned.

The webhook (`webhook.py:69-92`): parses the WhatsApp reply to `"approve"`/`"reject"` (`parse_decision`, webhook.py:22-29), then:

```python
thread_id = registry.resolve_oldest(params.get("From", ""))   # webhook.py:82
...
resume(graph, thread_id=thread_id, decision=decision)          # webhook.py:86  (resume defaults to resume_document, webhook.py:48)
```

`registry.resolve_oldest(phone)` (`PendingApprovals`, approvals.py:30-44) returns the oldest pending `thread_id` for that phone number (FIFO via rowid) and marks it resolved — its SQLite connection also uses `check_same_thread=False` (approvals.py:14). So webhook.py:86 is exactly the same second-invoke-on-the-same-thread mechanism as `resume_document` (runner.py:87); the only difference is the `thread_id` comes from the phone-number registry instead of a direct CLI call. The pending row is registered earlier by `request_invoice_approval` (runner.py:193-204): it calls `start_document`, and if a payload came back, `registry.add(thread_id, phone)` then sends the approval request.

> Breakpoint: webhook.py:82 to watch a cross-process resume pick the right thread, then webhook.py:86 to see it re-enter `resume_document` on a SQLite-persisted checkpoint.

---

## The mental model in one paragraph

`interrupt()` is a **resumable yield keyed by `thread_id`**. On the first pass (Phase 1) it throws out to `graph.invoke`, which returns normally carrying `result["__interrupt__"][0].value`; the node body up to and including the interrupt has run, but the node's `return` has not. A human (sync `decide` or async WhatsApp) produces a decision string. On the second pass (Phase 2) a fresh `graph.invoke(Command(resume=decision), config)` on the **same `thread_id`** reloads the checkpoint, **re-runs the entire `human_review` body from the top**, `interrupt()` now returns `decision`, the node writes `human_decision`, and routing continues to `book` (only on exact `"approve"`) or END. Two invokes, one logical run — joined solely by `thread_id` and the checkpointer.