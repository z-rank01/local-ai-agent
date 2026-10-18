"""Real page connections, reconnect grace, then drain through the unique exit path."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class AutoExitState:
    enabled: bool = True
    timeout_seconds: float = 15.0
    last_heartbeat: float | None = None  # time.monotonic() of the last page ping
    armed: bool = False  # True once any heartbeat arrived (page was opened)
    pages: set = field(default_factory=set)
    exit_error: str = ''

    def page_opened(self, identity, now):
        self.pages.add(identity)
        self.exit_error = ''
        self.observe_heartbeat(now)

    def page_closed(self, identity, now):
        self.pages.discard(identity)
        if not self.pages:
            self.last_heartbeat = now

    def observe_heartbeat(self, now: float) -> None:
        self.last_heartbeat = now
        self.armed = True

    def disarm(self) -> None:
        """Forget the page (e.g. an explicit user exit already ran)."""
        self.armed = False
        self.last_heartbeat = None

    def should_exit(self, now: float, **_legacy_diagnostics) -> bool:
        # Active tasks must be stopped/drained, never used as an infinite keepalive.
        if self.pages or self.exit_error:
            return False
        if not self.enabled or not self.armed or self.last_heartbeat is None:
            return False
        if now - self.last_heartbeat < self.timeout_seconds:
            return False
        return True
