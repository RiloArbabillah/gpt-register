import os
import unittest
from pathlib import Path
from unittest.mock import patch

import sentinel_quickjs as sentinel


class _Response:
    url = "https://auth.openai.com/?sv=20260804abc123"
    text = ""


class _Session:
    def get(self, url, **kwargs):
        return _Response()


class SentinelQuickJsTests(unittest.TestCase):
    def test_discovers_version_from_bootstrap_url(self):
        with patch.dict(os.environ, {"OPENAI_SENTINEL_AUTO_DISCOVER": "1"}):
            self.assertEqual(
                sentinel._discover_version(_Session(), 10000),
                "20260804abc123",
            )

    def test_retries_when_so_token_is_missing(self):
        sdk_file = Path("/private/tmp/gpt-register-sentinel-test/sdk.js")
        sdk_file.parent.mkdir(parents=True, exist_ok=True)
        sdk_file.write_text("sdk", encoding="utf-8")
        calls = {"solve": 0}

        def run_action(*, action, **kwargs):
            if action == "requirements":
                return {"request_p": "request"}
            calls["solve"] += 1
            return {
                "token": "main",
                "so_token": "" if calls["solve"] == 1 else "observer",
            }

        with patch.dict(os.environ, {
            "OPENAI_SENTINEL_RETRY_COUNT": "1",
            "OPENAI_SENTINEL_AUTO_DISCOVER": "0",
            "OPENAI_SENTINEL_CACHE_DIR": "/private/tmp/gpt-register-sentinel-test",
        }), patch.object(sentinel, "_ensure_sdk_file", return_value=sdk_file), \
                patch.object(sentinel, "_fetch_sentinel_challenge", return_value={"token": "challenge"}), \
                patch.object(sentinel, "_run_quickjs_action", side_effect=run_action):
            result = sentinel.get_sentinel_token_via_quickjs(
                object(), "device", timeout_ms=10000, log=lambda _: None,
            )

        self.assertEqual(result, ("main", "observer"))
        self.assertEqual(calls["solve"], 2)


if __name__ == "__main__":
    unittest.main()
