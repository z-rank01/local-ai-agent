"""Optional deterministic presentation of an already authorized tool receipt."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ReceiptPresentation:
    text: str
    audit_event: str
    version: str


class ReceiptPresenter(Protocol):
    def present_receipt(self, tool: str, arguments: dict, receipt: object, *,
                        question: str, read_receipts: list) -> ReceiptPresentation | None:
        """Return a verified display or None; never execute tools or authorize access."""
