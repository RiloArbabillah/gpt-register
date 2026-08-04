"""Unit tests for rate-limit error classification used by RT recovery."""

from __future__ import annotations

import unittest

from webui.registrar import classify_error


class RateLimitClassifyTest(unittest.TestCase):
    def test_http_429_is_rate_limit(self):
        err = "authorize/continue failed (screen_hint=signup): HTTP 429 req_id=abc"
        self.assertEqual(classify_error(err), "rate_limit")

    def test_too_many_requests_is_rate_limit(self):
        self.assertEqual(classify_error("Too many requests, retry later"), "rate_limit")

    def test_skipped_rate_limited_marker_is_rate_limit(self):
        self.assertEqual(
            classify_error("skipped_rate_limited: account is in rate-limit cooldown"),
            "rate_limit",
        )

    def test_http_409_remains_unknown(self):
        err = "authorize/continue failed (screen_hint=signup): HTTP 409 req_id=abc"
        self.assertEqual(classify_error(err), "unknown")

    def test_sentinel_missing_still_wins(self):
        err = "sentinel_so_token_missing: HTTP 429 during sentinel solve"
        self.assertEqual(classify_error(err), "sentinel_so_token_missing")


if __name__ == "__main__":
    unittest.main()
