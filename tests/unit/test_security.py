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
