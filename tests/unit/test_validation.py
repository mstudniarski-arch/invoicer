from invoicer.validation import nip_checksum_valid


def test_valid_nip_plain():
    assert nip_checksum_valid("5260001246") is True


def test_valid_nip_with_formatting():
    assert nip_checksum_valid("526-000-12-46") is True


def test_invalid_nip_bad_checksum():
    assert nip_checksum_valid("5260001247") is False


def test_invalid_nip_wrong_length():
    assert nip_checksum_valid("12345") is False


def test_invalid_nip_control_equals_ten():
    # Pierwsze 9 cyfr daje sume wazona ≡ 10 mod 11 → NIP niepoprawny z definicji.
    assert nip_checksum_valid("9000000001") is False


def test_none_nip_is_invalid():
    assert nip_checksum_valid(None) is False
