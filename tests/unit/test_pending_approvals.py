from invoicer.approvals import PendingApprovals


def _registry(tmp_path):
    return PendingApprovals(str(tmp_path / "pending.sqlite"))


def test_resolve_oldest_returns_fifo_then_none(tmp_path):
    reg = _registry(tmp_path)
    reg.add("t1", "whatsapp:+48500")
    reg.add("t2", "whatsapp:+48500")
    assert reg.resolve_oldest("whatsapp:+48500") == "t1"
    assert reg.resolve_oldest("whatsapp:+48500") == "t2"
    assert reg.resolve_oldest("whatsapp:+48500") is None


def test_resolve_oldest_unknown_phone_returns_none(tmp_path):
    assert _registry(tmp_path).resolve_oldest("whatsapp:+999") is None


def test_phones_are_isolated(tmp_path):
    reg = _registry(tmp_path)
    reg.add("a1", "whatsapp:+48500")
    reg.add("b1", "whatsapp:+48600")
    assert reg.resolve_oldest("whatsapp:+48600") == "b1"
    assert reg.resolve_oldest("whatsapp:+48500") == "a1"


def test_count_pending_counts_only_pending_status(tmp_path):
    reg = PendingApprovals(str(tmp_path / "p.sqlite"))
    reg.add("t1", "whatsapp:+48111")
    reg.add("t2", "whatsapp:+48111")
    reg.add("t3", "whatsapp:+48222")
    assert reg.count_pending() == 3
    assert reg.count_pending(phone="whatsapp:+48111") == 2
    reg.resolve_oldest("whatsapp:+48111")  # -> "t1" resolved
    assert reg.count_pending() == 2
    assert reg.count_pending(phone="whatsapp:+48111") == 1
