"""Unit tests for Proxyscrape proxy rotation / reuse-cooldown logic."""

from __future__ import annotations

import unittest

import proxy_proxyscrape as pp


class ProxyRotationTest(unittest.TestCase):
    def setUp(self):
        pp._proxies = ["a", "b", "c"]
        pp._use_history = {}
        pp._next_index = 0
        pp._PROXY_REUSE_COOLDOWN_SECONDS = 600
        pp._PROXY_MAX_USES_PER_WINDOW = 2
        pp._PROXY_RATE_LIMIT_WINDOW_SECONDS = 1800
        pp._blocked_until = {}

    def test_rotates_through_pool(self):
        picked = {pp._pick_proxy_locked(1000.0) for _ in range(6)}
        self.assertEqual(picked, {"a", "b", "c"})

    def test_skips_recently_used_when_alternatives_exist(self):
        # "a" was used 1 second ago, still inside the 600s cooldown.
        pp._use_history = {"a": [999.0]}
        pp._next_index = 0
        first = pp._pick_proxy_locked(1000.0)
        self.assertNotEqual(first, "a")
        self.assertIn(first, {"b", "c"})

    def test_falls_back_to_least_recently_used_when_all_are_hot(self):
        # All three were used within the cooldown; "c" is the oldest use.
        pp._use_history = {"a": [999.0], "b": [999.9], "c": [401.0]}
        pp._next_index = 0
        self.assertEqual(pp._pick_proxy_locked(1000.0), "c")

    def test_cooldown_expired_is_reusable(self):
        # "a" was used 700 seconds ago (> 600), so it is available again.
        pp._use_history = {"a": [300.0]}
        pp._next_index = 0
        self.assertEqual(pp._pick_proxy_locked(1000.0), "a")

    def test_skips_proxy_over_rate_limit_even_after_cooldown(self):
        # "a" was used twice inside the window, so it is over the per-IP limit
        # even though its last use is outside the cooldown.
        pp._PROXY_REUSE_COOLDOWN_SECONDS = 0
        pp._use_history = {"a": [100.0, 900.0]}
        pp._next_index = 0
        self.assertNotEqual(pp._pick_proxy_locked(1000.0), "a")

    def test_use_count_resets_after_window(self):
        # Uses older than the rate-limit window are pruned, so "a" is eligible.
        pp._PROXY_REUSE_COOLDOWN_SECONDS = 0
        pp._PROXY_RATE_LIMIT_WINDOW_SECONDS = 1800
        pp._use_history = {"a": [100.0, 500.0]}
        pp._next_index = 0
        self.assertEqual(pp._pick_proxy_locked(3000.0), "a")

    def test_all_over_limit_falls_back_to_least_recently_used(self):
        pp._PROXY_REUSE_COOLDOWN_SECONDS = 0
        pp._PROXY_MAX_USES_PER_WINDOW = 2
        pp._use_history = {
            "a": [100.0, 500.0],
            "b": [200.0, 600.0],
            "c": [300.0, 700.0],
        }
        pp._next_index = 0
        # "a" has the oldest last use (500) and is the least-recently used.
        self.assertEqual(pp._pick_proxy_locked(1000.0), "a")

    def test_marked_rate_limited_proxy_is_skipped(self):
        pp._blocked_until = {"a": 1600.0}
        pp._next_index = 0
        first = pp._pick_proxy_locked(1000.0)
        self.assertNotEqual(first, "a")
        self.assertIn(first, {"b", "c"})

    def test_all_blocked_returns_empty(self):
        pp._blocked_until = {p: 1600.0 for p in ("a", "b", "c")}
        self.assertEqual(pp._pick_proxy_locked(1000.0), "")

    def test_block_expires_and_proxy_is_reusable(self):
        pp._blocked_until = {"a": 900.0}
        pp._next_index = 0
        self.assertEqual(pp._pick_proxy_locked(1000.0), "a")

    def test_mark_proxy_rate_limited_records_block(self):
        with self.assertLogs("proxy_proxyscrape", level="WARNING"):
            pp.mark_proxy_rate_limited("b", block_seconds=600)
        self.assertIn("b", pp._blocked_until)
        self.assertGreater(pp._blocked_until["b"], 0)

    def test_empty_pool_returns_empty(self):
        pp._proxies = []
        self.assertEqual(pp._pick_proxy_locked(1000.0), "")


if __name__ == "__main__":
    unittest.main()
