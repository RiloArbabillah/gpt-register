"""Default proxy source backed by Proxyscrape's public free-proxy feed."""
from __future__ import annotations

import logging
import json
import os
import random
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from http_client import create_http_session

logger = logging.getLogger(__name__)

PROXY_SOURCE_URL = (
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies"
    "&format=json&timeout=20000"
)
_PROXY_PATTERN = re.compile(
    r"(?:https?://)?((?:\d{1,3}\.){3}\d{1,3}):(\d{2,5})",
    re.IGNORECASE,
)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


# How long one downloaded+probed proxy pool is reused before a refresh.
_CACHE_SECONDS = max(60, _env_int("PROXY_CACHE_SECONDS", 300))
# A proxy returned within this many seconds is skipped so the same IP is not
# reused too often. 0 disables the cooldown (legacy behavior).
_PROXY_REUSE_COOLDOWN_SECONDS = max(0, _env_int("PROXY_REUSE_COOLDOWN_SECONDS", 600))
# Maximum number of times a proxy may be handed out within the rate-limit
# window before it is skipped (per-IP registration rate limit).
_PROXY_MAX_USES_PER_WINDOW = max(1, _env_int("PROXY_MAX_USES_PER_WINDOW", 2))
# Sliding window (seconds) for the per-IP use count.
_PROXY_RATE_LIMIT_WINDOW_SECONDS = max(60, _env_int("PROXY_RATE_LIMIT_WINDOW_SECONDS", 1800))
_lock = threading.Lock()
_proxies: list[str] = []
_expires_at = 0.0
_next_index = 0
_use_history: dict[str, list[float]] = {}
_PROBE_URL = "https://chatgpt.com/api/auth/csrf"
_PROBE_SAMPLE_SIZE = 12
_FAST_PROXY_POOL_SIZE = 5

# Only proxies geolocated in one of these countries may be used.  Keep this
# list as ISO 3166-1 alpha-2 codes because that is the format ProxyScrape
# returns in each record's ``ip_data.countryCode`` field.
_ALLOWED_COUNTRY_CODES = frozenset(
    """
    AL DZ AF AX AD AO AG AR AM AW AU AT AZ BS BH BD BB BE BZ BM BJ BT BO BA
    BW BR BN BG BF BI CV KH CM CA KY CF TD CL CO KM CG CD CR CI HR CY CZ DK
    DJ DM DO EC EG SV GQ ER EE SZ ET FO FJ FI FR GF PF TF GA GM GE DE GH GR
    GD GL GT GP GN GW GY HT VA HN HU IS IN ID IQ IE IL IT JM JP JO KZ KE KI
    KW KG LA LV LB LS LR LY LI LT LU MG MW MY MV ML MT MH MQ MR MU YT MX FM
    MD MC MN ME MA MZ MM NA NR NP NL NC NZ NI NE NG MK NO OM PK PW PS PA PG
    PY PE PH PL PT QA RE RO RW BL SH KN LC MF PM VC WS SM ST SA SN RS SC SL
    SG SK SI SB SO ZA KR SS ES LK SR SE CH SD SJ TW TJ TZ TH TL TG TO TT TN
    TR TM TV UG UA AE GB US UY UZ VU VN WF YE ZM ZW
    """.split()
)
_ALLOWED_COUNTRIES_QUERY = ",".join(sorted(_ALLOWED_COUNTRY_CODES))


def _download(protocol: str) -> list[str]:
    """Download allowed HTTP or SOCKS5 proxies from ProxyScrape."""
    if protocol not in {"http", "socks5"}:
        raise ValueError(f"Unsupported ProxyScrape protocol: {protocol}")

    ssl_filter = "&ssl=yes" if protocol == "http" else ""
    request = urllib.request.Request(
        (
            f"{PROXY_SOURCE_URL}&protocol={protocol}{ssl_filter}"
            f"&country={_ALLOWED_COUNTRIES_QUERY}"
        ),
        headers={"User-Agent": "gpt-register/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = response.read().decode("utf-8", errors="replace")

    data = json.loads(payload)
    records = data.get("proxies", [])
    if not isinstance(records, list):
        raise ValueError("ProxyScrape response has an invalid proxies field")

    proxies: list[str] = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        if str(record.get("protocol") or "").lower() != protocol:
            continue
        country_code = str((record.get("ip_data") or {}).get("countryCode") or "").upper()
        if country_code not in _ALLOWED_COUNTRY_CODES:
            continue

        match = _PROXY_PATTERN.fullmatch(str(record.get("proxy") or "").strip())
        if not match:
            continue
        host, port = match.groups()
        octets = host.split(".")
        if any(int(octet) > 255 for octet in octets):
            continue
        proxy = f"{protocol}://{host}:{port}"
        if proxy not in seen:
            seen.add(proxy)
            proxies.append(proxy)

    logger.info(
        "[proxy] accepted %d Proxyscrape %s proxies from allowed countries",
        len(proxies),
        protocol,
    )
    return proxies


def _probe_https_proxy(proxy: str) -> tuple[str, float] | None:
    """Return latency when an HTTP CONNECT or SOCKS5 proxy reaches HTTPS."""
    started_at = time.monotonic()
    try:
        session = create_http_session(proxy=proxy)
        # Any HTTP status means the proxy connection reached chatgpt.com.
        session.get(_PROBE_URL, headers={"User-Agent": "gpt-register/1.0"}, timeout=8)
        return proxy, time.monotonic() - started_at
    except Exception as exc:
        logger.debug("[proxy] rejected unusable HTTPS proxy %s: %s", proxy, exc)
        return None


def _select_fast_proxies(proxies: list[str]) -> list[str]:
    candidates = proxies[:_PROBE_SAMPLE_SIZE]
    if not candidates:
        return []

    successful: list[tuple[str, float]] = []
    with ThreadPoolExecutor(max_workers=len(candidates), thread_name_prefix="proxy-probe") as executor:
        futures = [executor.submit(_probe_https_proxy, proxy) for proxy in candidates]
        for future in as_completed(futures):
            result = future.result()
            if result:
                successful.append(result)

    successful.sort(key=lambda item: item[1])
    selected = successful[:_FAST_PROXY_POOL_SIZE]
    logger.info(
        "[proxy] selected %d fast HTTPS proxies from %d probes: %s",
        len(selected),
        len(candidates),
        ", ".join(f"{latency * 1000:.0f}ms" for _, latency in selected),
    )
    return [proxy for proxy, _ in selected]


def _record_use_locked(proxy: str, now: float) -> None:
    """Record a proxy use, keeping only timestamps inside the rate-limit window."""
    history = [t for t in _use_history.get(proxy, []) if now - t < _PROXY_RATE_LIMIT_WINDOW_SECONDS]
    history.append(now)
    _use_history[proxy] = history


def _pick_proxy_locked(now: float) -> str:
    """Return the next proxy, skipping IPs that are over the per-IP rate limit.

    A proxy is eligible when it has not been used more than
    ``_PROXY_MAX_USES_PER_WINDOW`` times inside the sliding window and its last
    use is outside the reuse cooldown. If no proxy is eligible, fall back to the
    least-recently-used one (cooldown first, then rate limit) so a busy loop
    does not hard-fail on proxy exhaustion.
    """
    global _next_index
    if not _proxies:
        return ""
    pool_size = len(_proxies)
    start = _next_index % pool_size
    _next_index = start + 1
    cooldown_fallback = ""
    cooldown_fallback_age = -1.0
    limit_fallback = ""
    limit_fallback_age = -1.0
    for offset in range(pool_size):
        candidate = _proxies[(start + offset) % pool_size]
        history = [t for t in _use_history.get(candidate, []) if now - t < _PROXY_RATE_LIMIT_WINDOW_SECONDS]
        _use_history[candidate] = history
        last = history[-1] if history else 0.0
        age = now - last
        uses = len(history)
        within_limit = uses < _PROXY_MAX_USES_PER_WINDOW
        if within_limit and (last == 0.0 or age >= _PROXY_REUSE_COOLDOWN_SECONDS):
            _record_use_locked(candidate, now)
            return candidate
        if within_limit and age > cooldown_fallback_age:
            cooldown_fallback, cooldown_fallback_age = candidate, age
        if age > limit_fallback_age:
            limit_fallback, limit_fallback_age = candidate, age
    if cooldown_fallback:
        logger.warning(
            "[proxy] all pool members are still in cooldown; reusing the least-recently-used proxy %s",
            cooldown_fallback,
        )
        _record_use_locked(cooldown_fallback, now)
        return cooldown_fallback
    if limit_fallback:
        logger.warning(
            "[proxy] per-IP rate limit reached for every pool member; reusing the least-recently-used proxy %s",
            limit_fallback,
        )
        _record_use_locked(limit_fallback, now)
        return limit_fallback
    return ""


def get_default_proxy() -> str:
    """Return a rotating HTTPS-capable HTTP CONNECT or SOCKS5 proxy.

    The pool is cached and shuffled on refresh, and recently returned proxies are
    skipped for a cooldown period so the same IP is not reused too often. An
    empty result signals that registration must stop unless the caller
    explicitly selected direct connection.
    """
    global _proxies, _expires_at, _next_index
    with _lock:
        now = time.monotonic()
        if now >= _expires_at:
            fresh: list[str] = []
            try:
                http_proxies = _download("http")
                socks5_proxies = _download("socks5")
                fresh = _select_fast_proxies(
                    http_proxies[:_PROBE_SAMPLE_SIZE] + socks5_proxies[:_PROBE_SAMPLE_SIZE]
                )
            except Exception as exc:
                logger.warning("[proxy] Proxyscrape download failed: %s", exc)
            if fresh:
                random.shuffle(fresh)
                _proxies = fresh
                _expires_at = now + _CACHE_SECONDS
                _next_index = 0
                # Drop usage history for proxies that are no longer in the pool.
                for proxy in list(_use_history):
                    if proxy not in _proxies:
                        _use_history.pop(proxy, None)
                logger.info("[proxy] loaded %d Proxyscrape proxies", len(_proxies))
            elif _proxies:
                # A failed refresh should not hard-stop registration while a
                # known pool is still available; keep serving it for a minute.
                _expires_at = now + 60
                logger.warning(
                    "[proxy] Proxyscrape refresh failed; reusing %d cached proxies",
                    len(_proxies),
                )
            else:
                logger.warning("[proxy] Proxyscrape returned no usable HTTP CONNECT or SOCKS5 proxies")
                return ""

        return _pick_proxy_locked(time.monotonic())
