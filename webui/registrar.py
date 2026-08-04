"""注册 worker：调 auth_flow.run_register，并把日志/状态实时推到队列。

每个注册任务跑在独立线程；通过 `RunLogger` 把 `logging` 记录 + tail 状态推
到队列，前端用 SSE 实时收日志。
"""
from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]  # gpt-outlook-register/
sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402
from mail_outlook import OutlookMailProvider  # noqa: E402
from auth_flow import AuthFlow  # noqa: E402
from sms_provider import PhoneCallbackController  # noqa: E402

from . import db  # noqa: E402

# run_id -> queue of log strings; sentinel = None 表示流结束
_run_queues: dict[str, queue.Queue] = {}
_lock = threading.Lock()
_refresh_token_recoveries: set[str] = set()

LOG_DIR = Path(os.getenv("WEBUI_DATA_DIR", str(Path(__file__).resolve().parent))).resolve() / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


class QueueLogHandler(logging.Handler):
    """把 logging 记录扔进 run queue + 写 log 文件。"""

    def __init__(self, run_id: str, log_file: Path):
        super().__init__()
        self.run_id = run_id
        self._fh = open(log_file, "a", encoding="utf-8")
        self.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        ))

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            self._fh.write(msg + "\n")
            self._fh.flush()
            q = _run_queues.get(self.run_id)
            if q is not None:
                q.put(msg)
        except Exception:
            pass

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass
        super().close()


def _emit_status(run_id: str, kind: str, payload: dict | str = ""):
    """前端约定：以 `__EVENT__:` 开头的行被解析成 JSON 状态事件。"""
    import json as _json
    q = _run_queues.get(run_id)
    if q is None:
        return
    body = payload if isinstance(payload, dict) else {"message": str(payload)}
    body["kind"] = kind
    q.put("__EVENT__:" + _json.dumps(body, ensure_ascii=False))


# 网络/环境层错误特征：命中任一就把号放回 available（号本身没问题，是环境炸了）
_NETWORK_ERROR_PATTERNS = [
    "tls", "ssl", "sslerror", "connection", "connect error", "timeout", "timed out",
    "proxy", "socks", "dns", "name resolution", "name or service",
    "cloudflare", "just a moment", "403 forbidden",
    "csrf token 获取失败", "csrf token 失败",
    "/sentinel/req", "sentinel /req", "sentinel quickjs",
    "check_proxy 失败", "网络预检查",
    "curl: (35)", "curl: (28)", "curl: (6)", "curl: (7)",
    "curl: (52)", "curl: (55)", "curl: (56)", "curl: (57)", "curl: (58)",
    "network_error phase=",
    "remote disconnected", "connection reset", "connection aborted",
    "max retries exceeded",
    "invalid_state",
]


def classify_error(err: str) -> str:
    """分类错误：'network'（环境/代理问题，号无辜）/ 'account'（号本身有问题）/ 'unknown'。"""
    s = (err or "").lower()
    if "proxy_unavailable" in s:
        return "proxy_unavailable"
    if "sentinel_so_token_missing" in s:
        return "sentinel_so_token_missing"
    if any(p in s for p in ("429", "too many requests", "rate limit", "skipped_rate_limited")):
        return "rate_limit"
    # 先匹配 account 特征（更具体），避免子串误命中（如 "outlook OTP timeout" 含 "timeout"）
    if any(p in s for p in (
        "wrong_email_otp_code", "invalid_grant", "imap xoauth2",
        "outlook imap account unusable", "user is authenticated but not connected",
        "outlook refresh failed", "authentication failed", "authenticate failed",
        "outlook otp timeout", "registration_disallowed",
        "已有账号", "账号被", "refresh_token 失效",
    )):
        return "account"
    if any(p in s for p in _NETWORK_ERROR_PATTERNS):
        return "network"
    return "unknown"


def _do_register(
    run_id: str,
    account: dict,
    options: dict,
    log_file: Path,
):
    """实际注册任务。

    options:
        want_access_token: bool
        want_session_token: bool
        want_refresh_token: bool
        proxy: Optional[str]
        otp_timeout: int
        allow_existing_login: bool
    """
    handler = QueueLogHandler(run_id, log_file)
    handler.setLevel(logging.INFO)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    # 第一次需要的话提到 INFO 级别
    if root_logger.level > logging.INFO or root_logger.level == 0:
        root_logger.setLevel(logging.INFO)

    email = account["email"]
    saved_env = {}
    # 提前读取，避免在 try 块前异常时 except 引用未定义
    mail_source = db.get_setting("mail_source", "outlook")

    try:
        # 注入环境变量（不污染全局，跑完恢复）
        env_overrides = {}
        # outlook 接码邮箱常被 OpenAI 走 passwordless_signup 流程（新号收码而非设密码），
        # auth_flow 会误判为"已有账号"分支 → 不设 WEBUI_ALLOW_LOGIN 会 fast-fail。
        # 单号 WebUI 场景下 fast-fail 没意义（批量跑才需要"跳过被识别的号"），故强制 ON。
        env_overrides["WEBUI_ALLOW_LOGIN"] = "1"
        env_overrides["OTP_TIMEOUT"] = str(int(options.get("otp_timeout") or 180))
        # 用户不要 refresh_token → 直接跳过 Codex OAuth（每次都失败浪费 ~10s + 一堆告警）
        if not options.get("want_refresh_token", True):
            env_overrides["SKIP_OAUTH_TOKEN_EXCHANGE"] = "1"
            env_overrides["OAUTH_CODEX_RT_EXCHANGE"] = "0"
            env_overrides["OAUTH_CODEX_RT_BEFORE_CALLBACK"] = "0"
        # PROXY 走 cfg.proxy，无需 env
        for k, v in env_overrides.items():
            saved_env[k] = os.environ.get(k)
            os.environ[k] = v

        configured_proxy = (options.get("proxy") or "").strip()
        use_direct_connection = bool(options.get("use_direct_connection"))
        if use_direct_connection:
            selected_proxy = ""
            logging.getLogger("registrar").warning("[proxy] direct connection explicitly selected")
        elif configured_proxy:
            selected_proxy = configured_proxy
            logging.getLogger("registrar").info("[proxy] using manually configured proxy")
        else:
            from proxy_proxyscrape import get_default_proxy

            selected_proxy = get_default_proxy()
            if selected_proxy:
                logging.getLogger("registrar").info("[proxy] using default Proxyscrape proxy")
            else:
                raise RuntimeError(
                    "proxy_unavailable: no Proxyscrape proxy can establish an HTTPS CONNECT tunnel. "
                    "Check the connection or enable direct connection to continue."
                )

        cfg = Config()
        cfg.proxy = selected_proxy or None

        # ─ 邮箱来源路由：outlook 池 / CF Worker / IMAP catch-all ─
        if mail_source == "cf_temp":
            sys_path_root = str(ROOT)
            if sys_path_root not in sys.path:
                sys.path.insert(0, sys_path_root)
            from mail_cf import CFTempEmailProvider

            api_url = db.get_setting("cf_api_url", "")
            domain  = db.get_setting("cf_domain", "")
            token   = db.get_cf_admin_token()
            if not api_url or not domain or not token:
                raise RuntimeError(
                    "CF Temp Email is not fully configured (missing api_url / domain / admin_token). "
                    "Configure it in Mail Settings."
                )
            mail = CFTempEmailProvider(
                api_url=api_url, admin_token=token, domain=domain,
            )
            logging.getLogger("registrar").info(
                f"[register] 邮箱来源: cf_temp / domain={domain}"
            )
        elif mail_source == "imap":
            from mail_imap import ImapCatchAllProvider

            imap = db.get_imap_credentials()
            missing = [name for name in ("host", "username", "password", "domain") if not imap[name]]
            if missing:
                raise RuntimeError(
                    "IMAP catch-all is not fully configured (missing " + ", ".join(missing) + "). "
                    "Configure it in Mail Settings."
                )
            mail = ImapCatchAllProvider(
                host=imap["host"], username=imap["username"], password=imap["password"],
                domain=imap["domain"], port=int(imap["port"] or 993),
            )
            logging.getLogger("registrar").info(
                f"[register] 邮箱来源: imap / domain={imap['domain']}"
            )
        elif mail_source == "imap_pool":
            from mail_imap import ImapCatchAllProvider

            missing = [key for key in ("host", "password") if not account.get(key)]
            if missing:
                raise RuntimeError("IMAP mailbox configuration is incomplete (missing " + ", ".join(missing) + ")")
            mail = ImapCatchAllProvider(
                host=account["host"], username=account["email"], password=account["password"],
                domain=account["email"].rsplit("@", 1)[-1], port=int(account.get("port") or 993),
                mailbox_email=account["email"],
            )
            logging.getLogger("registrar").info(f"[register] mail source: imap_pool / email={email}")
        else:
            mail = OutlookMailProvider(
                email=account["email"],
                password=account.get("password", ""),
                client_id=account["client_id"],
                refresh_token=account["refresh_token"],
            )

        flow = AuthFlow(cfg, sms_callback=_build_sms_callback(run_id))
        _emit_status(run_id, "phase", {"phase": "starting", "email": email})
        logging.getLogger("registrar").info(f"[register] started: {email}")

        partial = False
        d: dict
        try:
            result = flow.run_register(mail)
            d = result.to_dict()
        except RuntimeError as e:
            # 部分凭证也算成功（OTP 验证通过 + create_account 成功 → flow.result 有 token）
            d = flow.result.to_dict()
            need_access = options.get("want_access_token", True)
            need_session = options.get("want_session_token", True)
            need_refresh = options.get("want_refresh_token", True)
            # 用户勾选的凭证全拿到 → 算正常完成（不视为 partial）
            wanted_ok = (
                (not need_access or d.get("access_token"))
                and (not need_session or d.get("session_token"))
                and (not need_refresh or d.get("refresh_token"))
            )
            has_any = bool(
                d.get("access_token") or d.get("refresh_token") or d.get("session_token")
            )
            if wanted_ok and has_any:
                logging.getLogger("registrar").warning(
                    f"[register] late-stage error but all requested credentials are available: {e}"
                )
            elif has_any:
                partial = True
                logging.getLogger("registrar").warning(
                    f"[register] partial credentials (a requested field is missing): {e}"
                )
            else:
                raise

        # ─ 用户选项过滤：未勾选的字段从结果里抹掉，DB 只存用户想要的
        full = d
        d = {
            "email": full.get("email", ""),
            "password": full.get("password", ""),
            "source": mail_source,
        }
        if options.get("want_access_token", True):
            d["access_token"] = full.get("access_token", "")
        if options.get("want_session_token", True):
            d["session_token"] = full.get("session_token", "")
            d["cookie_header"] = full.get("cookie_header", "")  # 同样是浏览器注入用
        if options.get("want_refresh_token", True):
            d["refresh_token"] = full.get("refresh_token", "")
            d["id_token"] = full.get("id_token", "")

        # 落库
        db.save_registered(d)
        # Catch-all 模式下 email 是虚拟占位，不操作 Outlook 号池。
        if mail_source == "imap_pool":
            db.mark_imap_done(email)
        elif mail_source not in ("cf_temp", "imap"):
            db.mark_done(email)

        # ─ 可选：导出到 CPA / SUB2API 面板（仅勾选启用时才执行） ─
        _try_export_to_panels(run_id, d)

        result_summary = {
            "email": d.get("email"),
            "access_token_len": len(d.get("access_token") or ""),
            "session_token_len": len(d.get("session_token") or ""),
            "refresh_token_len": len(d.get("refresh_token") or ""),
            "partial": partial,
        }
        _emit_status(run_id, "done", result_summary)
        logging.getLogger("registrar").info(
            f"[register] completed email={d.get('email')} "
            f"at={result_summary['access_token_len']} "
            f"st={result_summary['session_token_len']} "
            f"rt={result_summary['refresh_token_len']}"
        )
        db.finish_run(run_id, "done")

    except Exception as e:
        err = str(e)
        category = classify_error(err)
        logging.getLogger("registrar").error(f"[register] failed (category={category}): {err}")
        if category != "account":
            logging.getLogger("registrar").error(traceback.format_exc())
        environment_error = category in {"network", "sentinel_so_token_missing"}
        if mail_source == "imap_pool":
            if environment_error:
                db.release_imap_account(email)
            else:
                db.mark_imap_failed(email, f"[{category}] {err}")
        elif mail_source not in ("cf_temp", "imap"):
            if environment_error:
                db.release_unused(email)
                logging.getLogger("registrar").warning(
                    f"[register] {email} classified as a network/environment error; mailbox released to available"
                )
            else:
                db.mark_failed(email, f"[{category}] {err}")
        db.finish_run(run_id, "failed", err, category=category)
        _emit_status(run_id, "error", {"message": err, "category": category})

    finally:
        # 还原 env
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # 关闭 handler
        try:
            root_logger.removeHandler(handler)
            handler.close()
        except Exception:
            pass
        q = _run_queues.get(run_id)
        if q is not None:
            q.put(None)  # sentinel: 流结束


def _try_export_to_panels(run_id: str, cred: dict) -> None:
    """注册完成后可选地把凭证导出到 CPA / SUB2API 面板。

    - 任一目标的"启用"开关关闭时,该目标跳过(不发请求);两者都未启用时整段 no-op。
    - 任何异常都不抛,只 emit 日志/状态(不影响注册主流程)。
    """
    try:
        cfg = db.get_export_internal_config()
    except Exception as e:
        logging.getLogger("registrar").warning(f"[export] 读取配置失败: {e}")
        return

    cpa_enabled = bool(cfg.get("cpa", {}).get("enabled"))
    sub2api_enabled = bool(cfg.get("sub2api", {}).get("enabled"))
    if not (cpa_enabled or sub2api_enabled):
        return  # 用户没勾选任何目标 → 完全不执行

    from . import exporter  # 懒 import,避免未启用时强依赖

    explog = logging.getLogger("registrar")

    def _log(msg: str, level: str = "info") -> None:
        if level == "error":
            explog.error(f"[export] {msg}")
        elif level == "warn":
            explog.warning(f"[export] {msg}")
        else:
            explog.info(f"[export] {msg}")
        try:
            _emit_status(run_id, "phase", {"phase": "export", "message": msg, "level": level})
        except Exception:
            pass

    try:
        results = exporter.run_exports(
            cred,
            cpa_cfg=cfg.get("cpa") if cpa_enabled else None,
            sub2api_cfg=cfg.get("sub2api") if sub2api_enabled else None,
            log_fn=_log,
        )
    except Exception as e:
        _log(f"Export failed: {e}", "error")
        return

    # 汇总成一个事件给前端
    summary = {}
    if results.get("cpa") is not None:
        summary["cpa"] = {"ok": bool(results["cpa"].get("ok")),
                          "message": results["cpa"].get("message") or results["cpa"].get("error") or ""}
    if results.get("sub2api") is not None:
        summary["sub2api"] = {"ok": bool(results["sub2api"].get("ok")),
                              "message": results["sub2api"].get("message") or results["sub2api"].get("error") or ""}
    try:
        _emit_status(run_id, "phase", {"phase": "export_done", "summary": summary})
    except Exception:
        pass


def _build_sms_callback(run_id: str, *, force_reuse_three_times: bool = False) -> Optional[PhoneCallbackController]:
    """根据 webui 配置创建 SMS 接码 controller。

    未启用接码或未配置 API key 时返回 None，flow 会回退到环境变量路径。
    log_fn 把租号/等码的状态推到 SSE 流，前端可见。
    """
    cfg = db.get_sms_internal_config()
    if force_reuse_three_times:
        # Batch RT uses one cached number for at most three successful accounts.
        # SmsBower marks rejected numbers as non-reusable, so the next attempt
        # automatically rents a replacement number.
        cfg = {**cfg, "sms_reuse_phone": True, "sms_phone_success_max": "3"}
    if not cfg.get("sms_enabled"):
        return None
    api_key = (cfg.get("sms_api_key") or "").strip()
    if not api_key:
        logging.getLogger("registrar").warning("[sms] SMS is enabled but sms_api_key is not configured; skipping")
        return None

    smslog = logging.getLogger("registrar")

    def _log(msg: str) -> None:
        # 既写日志、又通过 _emit_status 推 phase 事件给前端
        smslog.info(f"[sms] {msg}")
        try:
            _emit_status(run_id, "phase", {"phase": "sms", "message": msg})
        except Exception:
            pass

    try:
        return PhoneCallbackController(
            provider_key=cfg["sms_provider"],
            config=cfg,
            service=cfg.get("sms_service") or "openai",
            country=cfg.get("sms_country") or "52",
            log_fn=_log,
            auto_select_country=bool(cfg.get("sms_auto_country")),
        )
    except Exception as e:
        smslog.warning(f"[sms] 创建接码 controller 失败: {e}")
        return None


def start_registration(account: dict, options: dict) -> str:
    """启动一次注册任务，返回 run_id。"""
    run_id = uuid.uuid4().hex[:12]
    log_file = LOG_DIR / f"{run_id}.log"
    db.create_run(run_id, account["email"], str(log_file))

    q: queue.Queue = queue.Queue()
    with _lock:
        _run_queues[run_id] = q

    th = threading.Thread(
        target=_do_register,
        args=(run_id, account, options, log_file),
        daemon=True,
        name=f"register-{run_id}",
    )
    th.start()
    return run_id


def _do_recover_refresh_token(
    run_id: str, registered: dict, account: Optional[dict], options: dict, log_file: Path,
    complete_run: bool = True, force_sms_reuse_three_times: bool = False,
):
    """Login ulang akun existing untuk melengkapi refresh token yang belum tersimpan."""
    handler = QueueLogHandler(run_id, log_file)
    handler.setLevel(logging.INFO)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    if root_logger.level > logging.INFO or root_logger.level == 0:
        root_logger.setLevel(logging.INFO)

    email = registered["email"]
    saved_env = {}
    try:
        if db.is_registered_rate_limited(email):
            raise RuntimeError(
                "skipped_rate_limited: account is in rate-limit cooldown; skip and retry later"
            )
        env_overrides = {
            "WEBUI_ALLOW_LOGIN": "1",
            "OTP_TIMEOUT": str(int(options.get("otp_timeout") or 180)),
            "OAUTH_CODEX_RT_EXCHANGE": "1",
            "OAUTH_CODEX_RT_BEFORE_CALLBACK": "1",
        }
        for key, value in env_overrides.items():
            saved_env[key] = os.environ.get(key)
            os.environ[key] = value

        configured_proxy = (options.get("proxy") or "").strip()
        if options.get("use_direct_connection"):
            selected_proxy = ""
            logging.getLogger("registrar").warning("[recover-rt] direct connection explicitly selected")
        elif configured_proxy:
            selected_proxy = configured_proxy
        else:
            from proxy_proxyscrape import get_default_proxy
            selected_proxy = get_default_proxy()
            if not selected_proxy:
                raise RuntimeError("proxy_unavailable: no proxy is available; enable direct connection or provide a proxy.")

        cfg = Config()
        cfg.proxy = selected_proxy or None
        password = registered.get("password") or (account or {}).get("password", "")
        if options.get("mail_source") == "imap":
            from mail_imap import ImapCatchAllProvider

            imap = db.get_imap_credentials()
            mail = ImapCatchAllProvider(
                host=imap["host"], username=imap["username"], password=imap["password"],
                domain=imap["domain"], port=int(imap["port"] or 993),
            )
            logging.getLogger("registrar").info("[recover-rt] using IMAP catch-all for OTP")
        elif options.get("mail_source") == "imap_pool":
            from mail_imap import ImapCatchAllProvider

            if not account:
                raise RuntimeError("IMAP mailbox pool account was not found")
            mail = ImapCatchAllProvider(
                host=account["host"], username=account["email"], password=account["password"],
                domain=account["email"].rsplit("@", 1)[-1], port=int(account.get("port") or 993),
                mailbox_email=account["email"],
            )
            logging.getLogger("registrar").info("[recover-rt] using IMAP pool mailbox for OTP")
        else:
            mail = OutlookMailProvider(
                email=email,
                password=password,
                client_id=(account or {}).get("client_id", ""),
                refresh_token=(account or {}).get("refresh_token", ""),
            )
        _emit_status(run_id, "phase", {"phase": "recovering_refresh_token", "email": email})
        logging.getLogger("registrar").info("[recover-rt] starting re-login: %s", email)
        result = AuthFlow(
            cfg,
            sms_callback=_build_sms_callback(run_id, force_reuse_three_times=force_sms_reuse_three_times),
        ).run_protocol_login(
            mail, email, password
        ).to_dict()
        refresh_token = result.get("refresh_token", "")
        if not refresh_token:
            raise RuntimeError("Login completed but refresh_token was not obtained")
        if not db.update_registered_refresh_token(email, refresh_token, result.get("id_token", "")):
            raise RuntimeError("refresh_token was not saved: the account already has an RT or the data was not found")

        # Reuse the configured CPA/SUB2API auto-export flow after a successful
        # RT recovery. Export errors are logged but do not invalidate the RT.
        recovered_cred = db.get_registered(email)
        if recovered_cred:
            _try_export_to_panels(run_id, recovered_cred)

        summary = {"email": email, "access_token_len": 0, "session_token_len": 0,
                   "refresh_token_len": len(refresh_token), "partial": False}
        if complete_run:
            db.finish_run(run_id, "done")
            _emit_status(run_id, "done", summary)
        logging.getLogger("registrar").info("[recover-rt] completed email=%s rt=%s", email, len(refresh_token))
    except Exception as e:
        error = str(e)
        category = classify_error(error)
        if category == "rate_limit":
            db.mark_registered_rate_limited(email)
        if complete_run:
            db.finish_run(run_id, "failed", error, category=category)
            _emit_status(run_id, "error", {"message": error, "category": category})
        logging.getLogger("registrar").error("[recover-rt] gagal: %s", error)
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            root_logger.removeHandler(handler)
            handler.close()
        except Exception:
            pass
        q = _run_queues.get(run_id)
        if q is not None and complete_run:
            q.put(None)
        with _lock:
            _refresh_token_recoveries.discard(email.lower())


def start_refresh_token_recovery(registered: dict, account: Optional[dict], options: dict) -> str:
    """Start a non-destructive RT recovery run for an existing registered account."""
    email = registered["email"].lower()
    with _lock:
        if email in _refresh_token_recoveries:
            raise RuntimeError("This account is already retrieving its refresh_token")
        _refresh_token_recoveries.add(email)

    run_id = uuid.uuid4().hex[:12]
    log_file = LOG_DIR / f"{run_id}.log"
    db.create_run(run_id, registered["email"], str(log_file))
    with _lock:
        _run_queues[run_id] = queue.Queue()
    threading.Thread(
        target=_do_recover_refresh_token,
        args=(run_id, registered, account, options, log_file),
        daemon=True,
        name=f"recover-rt-{run_id}",
    ).start()
    return run_id


def _do_recover_refresh_token_batch(run_id: str, entries: list[dict], options: dict, log_file: Path):
    succeeded = failed = 0
    try:
        for index, entry in enumerate(entries, start=1):
            registered = entry["registered"]
            email = registered["email"]
            logging.getLogger("registrar").info("[recover-rt] batch %s/%s: %s", index, len(entries), email)
            before = db.get_registered(email)
            _do_recover_refresh_token(
                run_id, registered, entry.get("account"),
                {**options, "mail_source": entry["mail_source"]}, log_file,
                complete_run=False, force_sms_reuse_three_times=True,
            )
            after = db.get_registered(email)
            if after and after.get("refresh_token") and not (before or {}).get("refresh_token"):
                succeeded += 1
            else:
                failed += 1
        db.finish_run(run_id, "done" if not failed else "failed", "" if not failed else f"{failed} accounts failed")
        _emit_status(run_id, "done", {
            "email": f"Batch RT recovery: {succeeded}/{len(entries)}", "access_token_len": 0,
            "session_token_len": 0, "refresh_token_len": succeeded, "partial": bool(failed),
        })
    finally:
        q = _run_queues.get(run_id)
        if q is not None:
            q.put(None)


def start_refresh_token_recovery_batch(entries: list[dict], options: dict) -> str:
    if not entries:
        raise RuntimeError("No accounts without refresh_token are available to process")
    emails = [entry["registered"]["email"].lower() for entry in entries]
    with _lock:
        busy = [email for email in emails if email in _refresh_token_recoveries]
        if busy:
            raise RuntimeError(f"An account is already retrieving refresh_token: {busy[0]}")
        _refresh_token_recoveries.update(emails)
    run_id = uuid.uuid4().hex[:12]
    log_file = LOG_DIR / f"{run_id}.log"
    db.create_run(run_id, f"batch-rt-{len(entries)}", str(log_file))
    with _lock:
        _run_queues[run_id] = queue.Queue()
    threading.Thread(
        target=_do_recover_refresh_token_batch, args=(run_id, entries, options, log_file),
        daemon=True, name=f"recover-rt-batch-{run_id}",
    ).start()
    return run_id


def get_run_queue(run_id: str) -> Optional[queue.Queue]:
    return _run_queues.get(run_id)


def remove_run_queue(run_id: str) -> None:
    with _lock:
        _run_queues.pop(run_id, None)
