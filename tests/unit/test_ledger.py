from invoicer.ledger import Ledger, LedgerEntry


def _entry(number: str, nip: str | None, name: str) -> LedgerEntry:
    return LedgerEntry(
        number=number,
        seller_nip=nip,
        seller_name=name,
        total_gross="1230.00",
        booking_id="MOCK-1",
        booked_at="2026-06-01T10:00:00",
    )


def test_append_and_read_roundtrip(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(_entry("FV/1", "5260001246", "ACME"))
    ledger.append(_entry("FV/2", "5260001246", "ACME"))
    entries = ledger.entries()
    assert [e.number for e in entries] == ["FV/1", "FV/2"]


def test_entries_empty_when_file_absent(tmp_path):
    ledger = Ledger(tmp_path / "missing.jsonl")
    assert ledger.entries() == []


def test_is_duplicate_matches_number_and_nip(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(_entry("FV/1", "5260001246", "ACME"))
    assert ledger.is_duplicate("FV/1", "5260001246", "ACME") is True
    assert ledger.is_duplicate("FV/9", "5260001246", "ACME") is False


def test_is_duplicate_falls_back_to_name_when_no_nip(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(_entry("INV/7", None, "Foreign Ltd"))
    assert ledger.is_duplicate("INV/7", None, "Foreign Ltd") is True
    assert ledger.is_duplicate("INV/7", None, "Other Ltd") is False


def test_append_builds_hash_chain_and_verifies(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.append(_entry("FV/1", "5260001246", "ACME"))
    ledger.append(_entry("FV/2", "5260001246", "ACME"))
    entries = ledger.entries()
    assert entries[0].prev_hash == ""
    assert entries[0].entry_hash  # niepuste
    assert entries[1].prev_hash == entries[0].entry_hash  # lancuch
    assert ledger.verify_chain() is True


def test_verify_chain_detects_tampering(tmp_path):
    path = tmp_path / "l.jsonl"
    ledger = Ledger(path)
    ledger.append(_entry("FV/1", "5260001246", "ACME"))
    ledger.append(_entry("FV/2", "5260001246", "ACME"))
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = lines[0].replace("1230.00", "9999.00")  # manipulacja kwoty
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert ledger.verify_chain() is False
