"""Tests for batch-registration cooldown limits and SSE countdown events."""
from __future__ import annotations

import unittest

from webui.auto_loop import (
    MAX_COOLDOWN_SECONDS,
    AutoLoopController,
    _cooldown_checkpoints,
    _normalize_cooldown,
)


class AutoLoopCooldownTest(unittest.TestCase):
    def test_cooldown_checkpoints_report_every_thirty_seconds(self):
        self.assertEqual(_cooldown_checkpoints(120), [120, 90, 60, 30])


    def test_cooldown_normalization_clamps_and_preserves_zero(self):
        self.assertEqual(_normalize_cooldown(0), 0)
        self.assertEqual(_normalize_cooldown(-5), 0)
        self.assertEqual(_normalize_cooldown(999), MAX_COOLDOWN_SECONDS)
        self.assertEqual(_normalize_cooldown(None), 3)


    def test_wait_cooldown_broadcasts_worker_countdown_events(self):
        controller = AutoLoopController()
        events = []
        controller._broadcast = lambda kind, data: events.append((kind, data))
        controller._interruptible_wait = lambda seconds: False

        controller._wait_cooldown(worker_id=2, seconds=120)

        self.assertEqual([kind for kind, _ in events], ["cooldown"] * 4)
        self.assertEqual([data["remaining"] for _, data in events], [120, 90, 60, 30])
        self.assertTrue(all(data["worker_id"] == 2 for _, data in events))
        self.assertTrue(all(data["total"] == 120 for _, data in events))


    def test_wait_cooldown_stops_without_broadcasting_when_interrupted(self):
        controller = AutoLoopController()
        events = []
        controller._broadcast = lambda kind, data: events.append((kind, data))
        controller._stop_event.set()

        controller._wait_cooldown(worker_id=0, seconds=120)

        self.assertEqual(events, [])
