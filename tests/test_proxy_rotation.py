"""Unit tests for Proxyscrape proxy rotation / reuse-cooldown logic."""

from __future__ import annotations

import unittest

import proxy_proxyscrape as pp


class ProxyRotationTest(unittest.TestCase):
    def setUp(self):
        pp._proxies = ["a", "b", "c"]
        pp._last_used_at = {}
        pp._next_index = 0
        pp._PROXY_REUSE_COOLDOWN_SECONDS = 600

    def test_rotates_through_pool(self):
        picked = {pp._pick_proxy_locked(1000.0) for _ in range(6)}
        self.assertEqual(picked, {"a", "b", "c"})

    def test_skips_recently_used_when_alternatives_exist(self):
        # "a" was used 1 second ago, still inside the 600s cooldown.
        pp._last_used_at = {"a": 999.0}
        pp._next_index = 0
        first = pp._pick_proxy_locked(1000.0)
        self.assertNotEqual(first, "a")
        self.assertIn(first, {"b", "c"})

    def test_falls_back_to_least_recently_used_when_all_are_hot(self):
        # All three were used within the cooldown; "c" is the oldest use.
        pp._last_used_at = {"a": 999.0, "b": 999.9, "c": 401.0}
        pp._next_index = 0
        self.assertEqual(pp._pick_proxy_locked(1000.0), "c")

    def test_cooldown_expired_is_reusable(self):
        # "a" was used 700 seconds ago (> 600), so it is available again.
        pp._last_used_at = {"a": 300.0}
        pp._next_index = 0
        self.assertEqual(pp._pick_proxy_locked(1000.0), "a")

    def test_empty_pool_returns_empty(self):
        pp._proxies = []
        self.assertEqual(pp._pick_proxy_locked(1000.0), "")


if __name__ == "__main__":
    unittest.main()
