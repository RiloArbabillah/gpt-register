import os
import unittest
from types import SimpleNamespace
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
    def test_runtime_status_uses_cached_version_without_network_session(self):
        with patch.dict(os.environ, {
            "OPENAI_SENTINEL_VERSION": "",
            "OPENAI_SENTINEL_CACHE_DIR": "/private/tmp/gpt-register-sentinel-status",
        }, clear=False), patch.object(
            sentinel.subprocess,
            "run",
            return_value=SimpleNamespace(stdout="v22.0.0\n"),
        ):
            status = sentinel.sentinel_runtime_status()

        self.assertEqual(status["version"], sentinel.DEFAULT_SENTINEL_VERSION)
        self.assertEqual(status["node_version"], "v22.0.0")
        self.assertIn("last_failure", status)
        self.assertIn("last_failure_at", status)

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

    def test_transport_error_stops_replaying_same_proxy(self):
        calls = {"download": 0}
        messages = []

        class CurlReceiveError(RuntimeError):
            code = 56

        def fail_download(*args, **kwargs):
            calls["download"] += 1
            raise CurlReceiveError(
                "Failed to perform, curl: (56) Connection closed abruptly"
            )

        with patch.dict(os.environ, {
            "OPENAI_SENTINEL_RETRY_COUNT": "4",
            "OPENAI_SENTINEL_AUTO_DISCOVER": "0",
        }), patch.object(sentinel, "_ensure_sdk_file", side_effect=fail_download):
            result = sentinel.get_sentinel_token_via_quickjs(
                object(), "device", timeout_ms=10000, log=messages.append,
            )

        self.assertIsNone(result)
        self.assertEqual(calls["download"], 1)
        self.assertTrue(any("phase=sdk_download" in message for message in messages))
        self.assertTrue(any("curl_code=56" in message for message in messages))
        self.assertIn("curl_code=56", sentinel.sentinel_last_failure())


if __name__ == "__main__":
    unittest.main()
