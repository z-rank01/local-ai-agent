"""Close-page auto-exit decision logic.

The BFF watches frontend heartbeats: once the page has been opened at least
once, closing it (heartbeat stops) starts a grace period, after which the
whole stack shuts itself down. Long-running work defers the exit instead of
being cut off.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class AutoExitState:
    enabled: bool = True
    timeout_seconds: float = 15.0
    last_heartbeat: float | None = None  # time.monotonic() of the last page ping
    armed: bool = False  # True once any heartbeat arrived (page was opened)

    def observe_heartbeat(self, now: float) -> None:
        self.last_heartbeat = now
        self.armed = True

    def disarm(self) -> None:
        """Forget the page (e.g. an explicit user exit already ran)."""
        self.armed = False
        self.last_heartbeat = None

    def should_exit(self, now: float, *, active_conversations: int,
                    stock_online: bool, stock_busy: bool) -> bool:
        if not self.enabled or not self.armed or self.last_heartbeat is None:
            return False
        if now - self.last_heartbeat < self.timeout_seconds:
            return False
        if active_conversations:
            return False
        if stock_online and stock_busy:
            return False
        return True
