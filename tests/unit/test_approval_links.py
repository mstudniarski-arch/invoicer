from invoicer.approval_links import build_decision_links, sign_decision, verify_decision

_SECRET = "s3cret-key"


def test_sign_is_deterministic_and_decision_specific():
    a = sign_decision(_SECRET, "t-1", "approve")
    b = sign_decision(_SECRET, "t-1", "approve")
    r = sign_decision(_SECRET, "t-1", "reject")
    assert a == b  # ten sam (secret, thread, decision) -> ten sam token
    assert a != r  # token approve != token reject (nie da sie podmienic decyzji)
    assert len(a) == 64  # hex sha256


def test_verify_accepts_correct_and_rejects_tampered():
    tok = sign_decision(_SECRET, "t-1", "approve")
    assert verify_decision(_SECRET, "t-1", "approve", tok) is True
    # zly token / inna decyzja / inny thread / inny secret -> odrzucone
    assert verify_decision(_SECRET, "t-1", "reject", tok) is False
    assert verify_decision(_SECRET, "t-2", "approve", tok) is False
    assert verify_decision("inny", "t-1", "approve", tok) is False
    assert verify_decision(_SECRET, "t-1", "approve", "deadbeef") is False
    assert verify_decision(_SECRET, "t-1", "approve", "") is False


def test_build_decision_links_have_valid_tokens_and_clean_base():
    links = build_decision_links("https://app.fly.dev/", "t-9", _SECRET)
    assert links["approve"].startswith("https://app.fly.dev/approve/t-9?t=")
    assert links["reject"].startswith("https://app.fly.dev/reject/t-9?t=")
    # trailing slash w base_url nie tworzy podwojnego //
    assert "fly.dev//" not in links["approve"]
    # tokeny z linkow przechodza weryfikacje
    at = links["approve"].split("t=")[1]
    rt = links["reject"].split("t=")[1]
    assert verify_decision(_SECRET, "t-9", "approve", at)
    assert verify_decision(_SECRET, "t-9", "reject", rt)
