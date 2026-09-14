"""Auto-exit on page close: decision-logic unit tests (no network, no Docker)."""

import unittest

from core.auto_exit import AutoExitState


class AutoExitStateTests(unittest.TestCase):
    def test_disarmed_before_first_heartbeat(self):
        state = AutoExitState(enabled=True, timeout_seconds=15)
        # The stack must stay up when the page was never opened.
        self.assertFalse(state.should_exit(1000.0, active_conversations=0,
                                           stock_online=False, stock_busy=False))

    def test_recent_heartbeat_keeps_stack_up(self):
        state = AutoExitState(enabled=True, timeout_seconds=15)
        state.observe_heartbeat(100.0)
        self.assertFalse(state.should_exit(114.0, active_conversations=0,
                                           stock_online=False, stock_busy=False))

    def test_exit_after_timeout_when_idle(self):
        state = AutoExitState(enabled=True, timeout_seconds=15)
        state.observe_heartbeat(100.0)
        self.assertTrue(state.should_exit(115.5, active_conversations=0,
                                          stock_online=False, stock_busy=False))

    def test_active_conversation_defers_exit(self):
        state = AutoExitState(enabled=True, timeout_seconds=15)
        state.observe_heartbeat(100.0)
        self.assertFalse(state.should_exit(120.0, active_conversations=1,
                                           stock_online=False, stock_busy=False))

    def test_busy_stock_defers_exit(self):
        state = AutoExitState(enabled=True, timeout_seconds=15)
        state.observe_heartbeat(100.0)
        self.assertFalse(state.should_exit(120.0, active_conversations=0,
                                           stock_online=True, stock_busy=True))

    def test_idle_online_stock_does_not_block_exit(self):
        state = AutoExitState(enabled=True, timeout_seconds=15)
        state.observe_heartbeat(100.0)
        self.assertTrue(state.should_exit(120.0, active_conversations=0,
                                          stock_online=True, stock_busy=False))

    def test_disarm_forgets_the_page(self):
        state = AutoExitState(enabled=True, timeout_seconds=15)
        state.observe_heartbeat(100.0)
        state.disarm()
        self.assertFalse(state.should_exit(10_000.0, active_conversations=0,
                                           stock_online=False, stock_busy=False))

    def test_disabled_never_exits(self):
        state = AutoExitState(enabled=False, timeout_seconds=15)
        state.observe_heartbeat(100.0)
        self.assertFalse(state.should_exit(10_000.0, active_conversations=0,
                                           stock_online=False, stock_busy=False))

    def test_heartbeat_rearms_after_reopen(self):
        state = AutoExitState(enabled=True, timeout_seconds=15)
        state.observe_heartbeat(100.0)
        self.assertTrue(state.should_exit(130.0, active_conversations=0,
                                          stock_online=False, stock_busy=False))
        state.observe_heartbeat(140.0)
        self.assertFalse(state.should_exit(145.0, active_conversations=0,
                                           stock_online=False, stock_busy=False))


if __name__ == '__main__':
    unittest.main()
