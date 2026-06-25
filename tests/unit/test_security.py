from invoicer.security import redact_pii


def test_redacts_nip():
    assert redact_pii("NIP 5260001246 sprzedawcy") == "NIP [NIP] sprzedawcy"


def test_redacts_bank_account():
    acc = "61109010140000071219812874"  # 26 cyfr (PL IBAN bez PL)
    assert "[KONTO]" in redact_pii(f"konto {acc}")
    assert acc not in redact_pii(f"konto {acc}")


def test_redacts_email():
    assert redact_pii("kontakt ksiegowa@klient.pl pilne") == "kontakt [EMAIL] pilne"


def test_passthrough_for_clean_text():
    assert redact_pii("Faktura krajowa, VAT 23%") == "Faktura krajowa, VAT 23%"


def test_redacts_multiple_pii_types():
    result = redact_pii("NIP 5260001246 kontakt ksiegowa@klient.pl")
    assert result == "NIP [NIP] kontakt [EMAIL]"


def test_redacts_pl_iban_grouped():
    out = redact_pii("przelew PL61 1090 1014 0000 0712 1981 2874 dzis")
    assert "[KONTO]" in out
    assert "1090" not in out


def test_redacts_account_grouped_without_prefix():
    out = redact_pii("konto 61 1090 1014 0000 0712 1981 2874")
    assert "[KONTO]" in out
    assert "1090" not in out


def test_redacts_pl_iban_compact():
    out = redact_pii("IBAN PL61109010140000071219812874 koniec")
    assert "[KONTO]" in out
    assert "6110901014" not in out


def test_redacts_nip_with_separators():
    assert redact_pii("NIP 526-000-12-46") == "NIP [NIP]"


def test_does_not_redact_iso_date_or_time():
    s = "2026-06-01 10:00:00 faktura VAT 23%"
    assert redact_pii(s) == s


def test_redact_pii_is_idempotent():
    s = "NIP 5260001246, konto 61109010140000071219812874, mail a@b.pl"
    once = redact_pii(s)
    assert redact_pii(once) == once


def test_redacts_pl_prefixed_vat_id():
    # unijny VAT ID PL = "PL" + 10-cyfrowy NIP (pole vat_id na fakturze)
    assert redact_pii("VAT PL5260001246 sprzedawcy") == "VAT [NIP] sprzedawcy"


def test_pl_prefixed_iban_still_konto_not_nip():
    # PL + 26 cyfr to nadal IBAN -> [KONTO], nie [NIP] (brak konfliktu z PL+10)
    out = redact_pii("IBAN PL61109010140000071219812874 koniec")
    assert "[KONTO]" in out
    assert "[NIP]" not in out


def test_redacts_twilio_account_sid():
    # Twilio Account SID (AC + 32 hex) nie moze trafic do logow/Sentry/alertow
    out = redact_pii("Twilio https://api.twilio.com/2010-04-01/Accounts/AC0123456789abcdef0123456789abcdef/Messages.json")
    assert "AC0123456789abcdef0123456789abcdef" not in out
    assert "[REDACTED_SID]" in out
