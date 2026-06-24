import base64

import pytest

from invoicer.bootstrap import bootstrap_gmail_token


def test_decodes_base64_env_to_destination(tmp_path, monkeypatch):
    token = b'{"refresh_token":"FAKE","token_uri":"https://oauth"}'
    monkeypatch.setenv("GMAIL_TOKEN_B64", base64.b64encode(token).decode("ascii"))
    dest = tmp_path / "token.json"
    bootstrap_gmail_token("GMAIL_TOKEN_B64", dest)
    assert dest.read_bytes() == token


def test_does_not_overwrite_existing_file(tmp_path, monkeypatch):
    dest = tmp_path / "token.json"
    dest.write_bytes(b"already-there")
    monkeypatch.setenv("GMAIL_TOKEN_B64", base64.b64encode(b"NEW").decode("ascii"))
    bootstrap_gmail_token("GMAIL_TOKEN_B64", dest)
    assert dest.read_bytes() == b"already-there"


def test_noop_when_env_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("GMAIL_TOKEN_B64", raising=False)
    dest = tmp_path / "token.json"
    bootstrap_gmail_token("GMAIL_TOKEN_B64", dest)
    assert not dest.exists()


def test_raises_on_invalid_base64(tmp_path, monkeypatch):
    monkeypatch.setenv("GMAIL_TOKEN_B64", "@@@nie-base64@@@")
    dest = tmp_path / "token.json"
    with pytest.raises(ValueError, match="GMAIL_TOKEN_B64"):
        bootstrap_gmail_token("GMAIL_TOKEN_B64", dest)
