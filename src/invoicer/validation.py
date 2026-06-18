from __future__ import annotations

NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)


def _digits_only(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def nip_checksum_valid(nip: str | None) -> bool:
    """Walidacja polskiego NIP algorytmem wagowym (mod 11).

    Suma kontrolna == 10 oznacza NIP niepoprawny (cyfra kontrolna nie moze byc 10).
    """
    if not nip:
        return False
    digits = _digits_only(nip)
    if len(digits) != 10:
        return False
    weighted = sum(int(digits[i]) * NIP_WEIGHTS[i] for i in range(9))
    control = weighted % 11
    if control == 10:
        return False
    return control == int(digits[9])
