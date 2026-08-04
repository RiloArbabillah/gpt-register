"""QuickJS-driven Sentinel token generator.

Adapted from
https://github.com/zc-zhangchen/any-auto-register
platforms/chatgpt/sentinel_browser.py:`_get_sentinel_token_via_quickjs`
+ scripts/js/openai_sentinel_quickjs.js (MIT License).

Why this exists:
  Pure-Python `sentinel.py` computes a synthetic PoW that *passes* OpenAI's
  surface validation (200 OK on `/sentinel/req`, `/authorize/continue`, etc.)
  but the OTP-dispatch service runs the actual sentinel SDK JS server-side
  to verify the token. Our synthetic token fails the deeper check → email
  silent-drop. To pass, we must run OpenAI's real `sdk.js` (downloaded from
  `sentinel.openai.com/sentinel/<ver>/sdk.js`) inside a JS VM and emit the
  same token the real browser would.

Implementation:
  - Spawn `node -e <wrapper>` per token request
  - Wrapper loads OpenAI's sdk.js + `openai_sentinel_quickjs.js` (a thin
    adapter that exposes `requirements`/`solve` actions over stdin/stdout)
  - Two passes: action=requirements → `request_p`, then `/sentinel/req` →
    challenge, then action=solve → `final_p` + `t`
  - Returns the same JSON-string shape `{p, t, c, id, flow}` as our
    pure-Python `build_sentinel_token`, so callers don't need to change

Public API:
  - `get_sentinel_token_via_quickjs(session, device_id, flow, ...) -> str | None`
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


DEFAULT_SENTINEL_VERSION = "20260219f9f6"
SENTINEL_REQ_URL = "https://sentinel.openai.com/backend-api/sentinel/req"
_TRANSPORT_CURL_CODES = frozenset({5, 6, 7, 18, 28, 35, 47, 52, 55, 56, 57, 58})


def _resolve_node_binary() -> str:
    return (os.getenv("OPENAI_SENTINEL_NODE_PATH", "") or "").strip() or "node"


def _quickjs_script_path() -> Path:
    return Path(__file__).resolve().parent / "openai_sentinel_quickjs.js"


_sdk_file_cache: Optional[Path] = None
_last_failure_detail = ""
_last_failure_at: Optional[float] = None


def sentinel_last_failure() -> str:
    """Return the latest non-secret failure detail for the caller."""
    return _last_failure_detail


def _cache_root() -> Path:
    configured = (os.getenv("OPENAI_SENTINEL_CACHE_DIR", "") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    data_dir = (os.getenv("WEBUI_DATA_DIR", "") or "").strip()
    if data_dir:
        return Path(data_dir).expanduser().resolve() / "sentinel"
    return Path(__file__).resolve().parent / "data" / "sentinel"


def _version_file() -> Path:
    return _cache_root() / "version.txt"


def _resolve_version() -> str:
    configured = (os.getenv("OPENAI_SENTINEL_VERSION", "") or "").strip()
    if configured:
        return configured
    try:
        cached = _version_file().read_text(encoding="utf-8").strip()
        if cached:
            return cached
    except OSError:
        pass
    return DEFAULT_SENTINEL_VERSION


def _curl_code(exc: BaseException) -> Optional[int]:
    code = getattr(exc, "code", None)
    try:
        if code is not None:
            return int(code)
    except (TypeError, ValueError):
        pass
    match = re.search(r"curl:\s*\((\d+)\)", str(exc), re.IGNORECASE)
    return int(match.group(1)) if match else None


def _is_transport_error(exc: BaseException) -> bool:
    code = _curl_code(exc)
    if code in _TRANSPORT_CURL_CODES:
        return True
    text = str(exc).lower()
    return any(marker in text for marker in (
        "connection closed", "connection reset", "connection aborted",
        "remote disconnected", "failed to perform",
    ))


def _format_phase_error(phase: str, exc: BaseException, endpoint: str = "") -> str:
    code = _curl_code(exc)
    code_text = f" curl_code={code}" if code is not None else ""
    endpoint_text = f" endpoint={endpoint}" if endpoint else ""
    return f"Sentinel QuickJS phase={phase}{code_text}{endpoint_text}: {str(exc)[:300]}"


def _discover_version(
    session: Any,
    timeout_ms: int,
    log: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """Best-effort discovery from bootstrap HTML; never blocks fallback startup."""
    if str(os.getenv("OPENAI_SENTINEL_AUTO_DISCOVER", "1")).lower() in {"0", "false", "no", "off"}:
        return None
    pattern = re.compile(r"(?:sentinel/|[?&]sv=)([A-Za-z0-9_-]{8,})")
    for url in ("https://auth.openai.com/", "https://chatgpt.com/auth/login"):
        try:
            response = session.get(url, timeout=max(5, int(timeout_ms / 1000)))
            source = f"{getattr(response, 'url', '')} {getattr(response, 'text', '')}"
            match = pattern.search(source)
            if match:
                return match.group(1)
        except Exception as exc:
            if log:
                log(_format_phase_error("bootstrap_discovery", exc, url))
            continue
    return None


def _sdk_url(version: str) -> str:
    configured = (os.getenv("OPENAI_SENTINEL_SDK_URL", "") or "").strip()
    if configured:
        return configured.replace("{version}", version)
    return f"https://sentinel.openai.com/sentinel/{version}/sdk.js"


def sentinel_runtime_status() -> dict:
    """Return non-secret runtime state for the admin diagnostics endpoint."""
    # Status must remain a fast, side-effect-free diagnostic endpoint. Runtime
    # discovery happens only when a token is requested with a live session.
    version = _resolve_version()
    sdk_file = _cache_root() / version / "sdk.js"
    node = _resolve_node_binary()
    try:
        node_version = subprocess.run(
            [node, "--version"], capture_output=True, text=True, timeout=5,
        ).stdout.strip() or None
        node_error = None
    except Exception as exc:
        node_version = None
        node_error = str(exc)[:180]
    return {
        "node_path": node,
        "node_version": node_version,
        "node_error": node_error,
        "version": version,
        "sdk_url": _sdk_url(version),
        "sdk_path": str(sdk_file),
        "sdk_cached": sdk_file.is_file() and sdk_file.stat().st_size > 0,
        "sdk_bytes": sdk_file.stat().st_size if sdk_file.is_file() else 0,
        "cache_dir": str(_cache_root()),
        "last_failure": _last_failure_detail,
        "last_failure_at": _last_failure_at,
    }


def _ensure_sdk_file(session: Any, timeout_ms: int, version: str) -> Path:
    """Download OpenAI's SDK to the persistent cache, once per version."""
    global _sdk_file_cache
    expected_file = _cache_root() / version / "sdk.js"
    if _sdk_file_cache and _sdk_file_cache == expected_file and _sdk_file_cache.exists():
        return _sdk_file_cache

    cache_dir = _cache_root() / version
    cache_dir.mkdir(parents=True, exist_ok=True)
    sdk_file = cache_dir / "sdk.js"
    if sdk_file.exists() and sdk_file.stat().st_size > 0:
        _sdk_file_cache = sdk_file
        return sdk_file

    resp = session.get(
        _sdk_url(version),
        headers={
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9",
            "referer": "https://auth.openai.com/",
            "sec-fetch-dest": "script",
            "sec-fetch-mode": "no-cors",
            "sec-fetch-site": "same-site",
        },
        timeout=max(10, int(timeout_ms / 1000)),
    )
    if getattr(resp, "status_code", 0) != 200:
        raise RuntimeError(f"Failed to download sdk.js: HTTP {resp.status_code}")
    content = getattr(resp, "content", b"") or (resp.text or "").encode()
    if not content:
        raise RuntimeError("Failed to download sdk.js: empty response")
    tmp_file = sdk_file.with_suffix(f".tmp-{os.getpid()}-{int(time.time() * 1000)}")
    tmp_file.write_bytes(content)
    tmp_file.replace(sdk_file)
    try:
        _version_file().parent.mkdir(parents=True, exist_ok=True)
        _version_file().write_text(version, encoding="utf-8")
    except OSError:
        pass
    _sdk_file_cache = sdk_file
    return sdk_file


def _run_quickjs_action(
    *,
    action: str,
    sdk_file: Path,
    quickjs_script: Path,
    payload: dict,
    timeout_ms: int,
) -> dict:
    body = dict(payload)
    body["action"] = action
    proc = subprocess.run(
        [_resolve_node_binary(), str(quickjs_script)],
        input=json.dumps(body, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=max(10, int(timeout_ms / 1000) + 5),
        env={
            **os.environ,
            "OPENAI_SENTINEL_SDK_FILE": str(sdk_file),
        },
    )
    if proc.returncode != 0:
        raise RuntimeError(f"QuickJS execution failed: {(proc.stderr or proc.stdout or 'unknown').strip()[:300]}")
    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError("QuickJS returned empty output")
    data = json.loads(out)
    if not isinstance(data, dict):
        raise RuntimeError("QuickJS output is not a JSON object")
    return data


def _fetch_sentinel_challenge(
    session: Any,
    *,
    device_id: str,
    flow: str,
    request_p: str,
    version: str,
    timeout_ms: int,
) -> dict:
    body = {"p": request_p, "id": device_id, "flow": flow}
    resp = session.post(
        SENTINEL_REQ_URL,
        data=json.dumps(body, separators=(",", ":")),
        headers={
            "origin": "https://sentinel.openai.com",
            "referer": f"https://sentinel.openai.com/backend-api/sentinel/frame.html?sv={version}",
            "content-type": "text/plain;charset=UTF-8",
            "accept": "*/*",
            "accept-encoding": "gzip, deflate, br, zstd",
            "accept-language": "zh-CN,zh;q=0.9",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
        },
        timeout=max(10, int(timeout_ms / 1000)),
    )
    if getattr(resp, "status_code", 0) != 200:
        raise RuntimeError(f"/sentinel/req HTTP {resp.status_code}")
    payload = resp.json()
    if not isinstance(payload, dict):
        raise RuntimeError("Sentinel challenge response is not a JSON object")
    return payload


def get_sentinel_token_via_quickjs(
    session: Any,
    device_id: str,
    *,
    flow: str = "authorize_continue",
    timeout_ms: int = 45000,
    log: Optional[Callable[[str], None]] = None,
    user_agent: str = "",
    screen: str = "",
    lang: str = "",
    lang_full: str = "",
    browser_type: str = "",
    platform: str = "",
    vendor: Optional[str] = None,
    hardware_concurrency: int = 0,
    device_memory: Optional[int] = None,
    max_touch_points: int = 0,
    device_pixel_ratio: float = 0.0,
    timezone: str = "",  # IANA 时区名（如 Asia/Tokyo）
    # Client Hints 全套（QuickJS 路径不直接用，但为了签名统一接收）
    sec_ch_ua_full_version_list: str = "",
    sec_ch_ua_arch: str = "",
    sec_ch_ua_bitness: str = "",
    sec_ch_ua_model: str = "",
    sec_ch_ua_platform_version: str = "",
) -> Optional[tuple[str, str]]:
    """Try the QuickJS path. Return JSON string on success, None on any failure.

    Caller is expected to fall back to pure-Python sentinel on None.

    指纹一致性：``platform`` / ``vendor`` / ``hardware_concurrency`` 等按调用方
    传入的浏览器家族画像喂给 sdk.js 的 navigator，避免 UA 说 Windows Chrome 但
    navigator 报 MacIntel/Apple 的硬伤。未传时按 UA 推断合理默认值。
    """
    global _sdk_file_cache, _last_failure_detail, _last_failure_at
    _last_failure_detail = ""
    _last_failure_at = None
    log = log or (lambda m: logger.info(m))
    quickjs_script = _quickjs_script_path()
    if not quickjs_script.exists():
        log(f"Sentinel QuickJS 脚本不存在: {quickjs_script}")
        _last_failure_detail = f"phase=quickjs_script_missing path={quickjs_script}"
        _last_failure_at = time.time()
        return None

    did = str(device_id or uuid.uuid4())

    screen_w, screen_h = "1920", "1080"
    if screen and "x" in screen:
        parts = screen.split("x", 1)
        screen_w, screen_h = parts[0], parts[1]

    lang_primary = lang or "en-US"
    languages = [lang_primary]
    if lang_full:
        for part in lang_full.split(","):
            tag = part.split(";")[0].strip()
            if tag and tag not in languages:
                languages.append(tag)

    # ── 指纹一致性：platform / vendor 未显式传入时按 UA 推断，绝不写死 MacIntel ──
    ua_l = (user_agent or "").lower()
    if not platform:
        if "iphone" in ua_l:
            platform = "iPhone"
        elif "windows" in ua_l:
            platform = "Win32"
        elif "mac" in ua_l:
            platform = "MacIntel"
        else:
            platform = "Win32"
    if vendor is None:
        if "firefox" in ua_l:
            vendor = ""                       # Firefox navigator.vendor 为空串
        elif "chrome" in ua_l:
            vendor = "Google Inc."
        else:
            vendor = "Apple Computer, Inc."   # Safari / iOS
    hw_conc = int(hardware_concurrency) if hardware_concurrency else 8

    env_payload = {
        "device_id": did,
        "user_agent": user_agent or "Mozilla/5.0",
        "screen_width": screen_w,
        "screen_height": screen_h,
        "language": lang_primary,
        "languages": languages,
        "platform": platform,
        "vendor": vendor,
        "hardware_concurrency": hw_conc,
        "browser_type": browser_type or "",
        "device_pixel_ratio": float(device_pixel_ratio) if device_pixel_ratio else 1.0,
        "max_touch_points": int(max_touch_points),
        "timezone": timezone or "UTC",  # IANA 时区名
    }
    # deviceMemory 仅 Chromium 暴露；None 时不下发该键，JS 侧保持 undefined
    if device_memory is not None:
        env_payload["device_memory"] = int(device_memory)

    version = _resolve_version()
    if not (os.getenv("OPENAI_SENTINEL_VERSION", "") or "").strip():
        discovered = _discover_version(session, timeout_ms, log=log)
        if discovered:
            version = discovered
    try:
        retry_count = max(0, min(5, int(os.getenv("OPENAI_SENTINEL_RETRY_COUNT", "2"))))
    except ValueError:
        retry_count = 2
    last_error = ""
    for attempt in range(1, retry_count + 2):
        phase = "sdk_download"
        endpoint = _sdk_url(version)
        try:
            sdk_file = _ensure_sdk_file(session, timeout_ms, version)
            phase = "quickjs_requirements"
            endpoint = "node://openai_sentinel_quickjs/requirements"
            requirements = _run_quickjs_action(
                action="requirements", sdk_file=sdk_file, quickjs_script=quickjs_script,
                payload=env_payload, timeout_ms=timeout_ms,
            )
            request_p = str(requirements.get("request_p") or "").strip()
            if not request_p:
                raise RuntimeError("requirements_missing_request_p")

            phase = "sentinel_challenge"
            endpoint = SENTINEL_REQ_URL
            challenge = _fetch_sentinel_challenge(
                session, device_id=did, flow=flow, request_p=request_p,
                version=version, timeout_ms=timeout_ms,
            )
            if not str(challenge.get("token") or "").strip():
                raise RuntimeError("challenge_missing_token")

            solve_payload = dict(env_payload)
            solve_payload.update({
                "request_p": request_p, "challenge": challenge,
                "flow": flow, "behavior_duration_ms": 4200,
            })
            phase = "quickjs_solve"
            endpoint = "node://openai_sentinel_quickjs/solve"
            solved = _run_quickjs_action(
                action="solve", sdk_file=sdk_file, quickjs_script=quickjs_script,
                payload=solve_payload, timeout_ms=timeout_ms,
            )
            sdk_token = str(solved.get("token") or "").strip()
            so_token_raw = str(solved.get("so_token") or "").strip()
            if sdk_token and so_token_raw:
                _last_failure_detail = ""
                _last_failure_at = None
                log(f"Sentinel QuickJS OK (version={version}, attempt={attempt}, len={len(sdk_token)}, so=Y)")
                return (sdk_token, so_token_raw)
            reason = "so_token_missing" if sdk_token else "sdk_token_missing"
            so_error = str(solved.get("so_error") or "").strip()
            detail = f" detail={so_error}" if so_error else ""
            last_error = f"phase=quickjs_solve reason={reason}{detail}"
            log(f"Sentinel QuickJS retryable failure: {reason}{detail} version={version} attempt={attempt}/{retry_count + 1}")
            if reason == "so_token_missing":
                sdk_file.unlink(missing_ok=True)
                if _sdk_file_cache == sdk_file:
                    _sdk_file_cache = None
        except Exception as exc:
            last_error = _format_phase_error(phase, exc, endpoint)
            log(f"{last_error} attempt={attempt}/{retry_count + 1}")
            # A receive/transport error means this proxy path is broken. Do not
            # spend all retries replaying the same dead connection.
            if _is_transport_error(exc):
                break
        if attempt <= retry_count:
            time.sleep(min(2.0, 0.5 * attempt))
    log(
        f"Sentinel QuickJS failed after {attempt} attempt(s): SO token unavailable"
        + (f"; last_error={last_error}" if last_error else "")
    )
    _last_failure_detail = last_error
    _last_failure_at = time.time()
    return None
