from __future__ import annotations

import operator
from typing import Annotated

from typing_extensions import TypedDict

from invoicer.booking import BookingResult
from invoicer.models import Classification, Invoice, InvoiceDocument, ValidationResult
from invoicer.rag.models import RetrievedChunk


class InvoiceState(TypedDict, total=False):
    """Stan przeplywajacy przez graf. total=False -> wezly zwracaja czesciowe aktualizacje."""

    document: InvoiceDocument
    invoice: Invoice | None
    validation: ValidationResult | None
    classification: Classification | None
    human_decision: str | None  # "approve" | "reject" | "edit"
    booking: BookingResult | None
    extract_attempts: int
    errors: Annotated[list[str], operator.add]
    legal_context: list[RetrievedChunk]
