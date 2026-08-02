"""Default proxy source backed by Proxyscrape's public free-proxy feed."""
from __future__ import annotations

import logging
import re
import threading
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

PROXY_SOURCE_URL = (
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies"
    "&protocol=http&ssl=yes&proxy_format=protocolipport"
    "&format=text&timeout=20000"
)
_PROXY_PATTERN = re.compile(
    r"(?:https?://)?((?:\d{1,3}\.){3}\d{1,3}):(\d{2,5})",
    re.IGNORECASE,
)
_CACHE_SECONDS = 300
_lock = threading.Lock()
_proxies: list[str] = []
_expires_at = 0.0
_next_index = 0
_PROBE_URL = "https://chatgpt.com/api/auth/csrf"
_MAX_PROBE_ATTEMPTS = 6


def _download() -> list[str]:
    request = urllib.request.Request(
        PROXY_SOURCE_URL,
        headers={"User-Agent": "gpt-register/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = response.read().decode("utf-8", errors="replace")

    proxies = []
    seen = set()
    for host, port in _PROXY_PATTERN.findall(payload):
        octets = host.split(".")
        if any(int(octet) > 255 for octet in octets):
            continue
        proxy = f"http://{host}:{port}"
        if proxy not in seen:
            seen.add(proxy)
            proxies.append(proxy)
    return proxies


def _supports_https_tunnel(proxy: str) -> bool:
    """Confirm the proxy accepts an HTTPS CONNECT tunnel to the registration site."""
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
    )
    request = urllib.request.Request(_PROBE_URL, headers={"User-Agent": "gpt-register/1.0"})
    try:
        with opener.open(request, timeout=8):
            return True
    except urllib.error.HTTPError:
        # Any HTTP response means the CONNECT tunnel reached chatgpt.com.
        return True
    except (OSError, ValueError, urllib.error.URLError) as exc:
        logger.debug("[proxy] rejected unusable HTTPS proxy %s: %s", proxy, exc)
        return False


def get_default_proxy() -> str:
    """Return a rotating HTTPS-capable Proxyscrape proxy.

    The source is cached briefly. An empty result signals that registration must
    stop unless the caller explicitly selected direct connection.
    """
    global _proxies, _expires_at, _next_index
    with _lock:
        now = time.monotonic()
        if now >= _expires_at:
            try:
                fresh = _download()
            except Exception as exc:
                logger.warning("[proxy] Proxyscrape download failed: %s", exc)
                return ""
            if not fresh:
                logger.warning("[proxy] Proxyscrape returned no usable HTTPS proxies")
                return ""
            _proxies = fresh
            _expires_at = now + _CACHE_SECONDS
            _next_index = 0
            logger.info("[proxy] loaded %d Proxyscrape proxies", len(_proxies))

        attempts = min(len(_proxies), _MAX_PROBE_ATTEMPTS)
        for _ in range(attempts):
            proxy = _proxies[_next_index % len(_proxies)]
            _next_index += 1
            if _supports_https_tunnel(proxy):
                return proxy

        logger.warning("[proxy] no Proxyscrape proxy accepted an HTTPS CONNECT tunnel")
        return ""
