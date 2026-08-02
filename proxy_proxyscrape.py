"""Default proxy source backed by Proxyscrape's public free-proxy feed."""
from __future__ import annotations

import logging
import json
import re
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

PROXY_SOURCE_URL = (
    "https://api.proxyscrape.com/v4/free-proxy-list/get?request=displayproxies"
    "&protocol=http&ssl=yes&format=json&timeout=20000"
)
_PROXY_PATTERN = re.compile(
    r"(?:https?://)?((?:\d{1,3}\.){3}\d{1,3}):(\d{2,5})",
    re.IGNORECASE,
)
_CACHE_SECONDS = 60
_lock = threading.Lock()
_proxies: list[str] = []
_expires_at = 0.0
_next_index = 0
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


def _download() -> list[str]:
    request = urllib.request.Request(
        f"{PROXY_SOURCE_URL}&country={_ALLOWED_COUNTRIES_QUERY}",
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
        proxy = f"http://{host}:{port}"
        if proxy not in seen:
            seen.add(proxy)
            proxies.append(proxy)

    logger.info(
        "[proxy] accepted %d Proxyscrape HTTPS proxies from allowed countries",
        len(proxies),
    )
    return proxies


def _probe_https_tunnel(proxy: str) -> tuple[str, float] | None:
    """Return proxy latency when it accepts an HTTPS CONNECT tunnel."""
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy}),
    )
    request = urllib.request.Request(_PROBE_URL, headers={"User-Agent": "gpt-register/1.0"})
    started_at = time.monotonic()
    try:
        with opener.open(request, timeout=8):
            return proxy, time.monotonic() - started_at
    except urllib.error.HTTPError:
        # Any HTTP response means the CONNECT tunnel reached chatgpt.com.
        return proxy, time.monotonic() - started_at
    except (OSError, ValueError, urllib.error.URLError) as exc:
        logger.debug("[proxy] rejected unusable HTTPS proxy %s: %s", proxy, exc)
        return None


def _select_fast_proxies(proxies: list[str]) -> list[str]:
    candidates = proxies[:_PROBE_SAMPLE_SIZE]
    if not candidates:
        return []

    successful: list[tuple[str, float]] = []
    with ThreadPoolExecutor(max_workers=len(candidates), thread_name_prefix="proxy-probe") as executor:
        futures = [executor.submit(_probe_https_tunnel, proxy) for proxy in candidates]
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
                fresh = _select_fast_proxies(_download())
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

        proxy = _proxies[_next_index % len(_proxies)]
        _next_index += 1
        return proxy
