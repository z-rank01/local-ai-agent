"""Optional provider-owned rules at the generic tool-call boundary."""
from typing import Protocol


class CallPolicy(Protocol):
    def validate(self, tool: str, arguments: dict, *, phase: str) -> None:
        """Reject a call before execution; does not authorize or execute it."""

    def ends_window(self, tool: str, receipt: object) -> bool:
        """Whether an actual receipt asks the host to stop this call batch."""
