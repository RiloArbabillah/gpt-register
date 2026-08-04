"""FastAPI 主程序：路由 + SSE 流式日志。

启动:
    python -m webui.app
或者:
    python start_webui.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from . import db, registrar  # noqa: E402
from .auto_loop import CONTROLLER as AUTO_LOOP  # noqa: E402

# 启动时自动释放卡死的 in_use 号（上次进程崩溃 / 强退留下的）
try:
    _released = db.release_stale_in_use(stale_seconds=1800)
    if _released > 0:
        logging.getLogger("webui").info(f"[startup] 释放 {_released} 个卡死的 in_use 号")
except Exception as _e:
    logging.getLogger("webui").warning(f"[startup] release_stale 失败: {_e}")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("webui")

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="GPT Outlook Register WebUI", docs_url=None, redoc_url=None)

AUTH_COOKIE = "gpt_register_session"
CSRF_COOKIE = "gpt_register_csrf"
PUBLIC_API_PATHS = {"/api/health", "/api/auth/login"}
ADMIN_API_PREFIXES = (
    "/api/settings", "/api/import", "/api/imap_accounts", "/api/admin/users",
)


def _secure_cookies() -> bool:
    return str(os.getenv("AUTH_COOKIE_SECURE", "0")).lower() in {"1", "true", "yes", "on"}


@app.middleware("http")
async def authentication_middleware(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api/") or path in PUBLIC_API_PATHS:
        return await call_next(request)
    token = request.cookies.get(AUTH_COOKIE, "")
    user = db.get_auth_session(token)
    if not user:
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    if any(path.startswith(prefix) for prefix in ADMIN_API_PREFIXES) and user["role"] != "admin":
        return JSONResponse({"detail": "Administrator access required"}, status_code=403)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        csrf = request.headers.get("X-CSRF-Token", "")
        if not csrf or not db.get_auth_session(token, csrf):
            return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
    request.state.user = user
    return await call_next(request)


# ──────────────────────── Pydantic 模型 ────────────────────────


class ImportReq(BaseModel):
    text: str = Field(..., description="多行 4 段格式 (email----password----client_id----refresh_token)")


class RegisterReq(BaseModel):
    email: Optional[str] = Field(None, description="留空 = 自动 claim 下一个 available")
    want_access_token: bool = True
    want_session_token: bool = True
    want_refresh_token: bool = True
    proxy: str = ""
    use_direct_connection: bool = False
    otp_timeout: int = 10
    allow_existing_login: bool = True


class LoginReq(BaseModel):
    username: str
    password: str


class CreateUserReq(BaseModel):
    username: str
    password: str
    role: str = "user"


class UpdateUserReq(BaseModel):
    role: Optional[str] = None
    password: Optional[str] = None
    is_active: Optional[bool] = None


# ──────────────────────── API ────────────────────────


@app.get("/api/health")
def health():
    return {"ok": True, "stats": db.stats()}


@app.get("/api/admin/sentinel/status")
def api_sentinel_status():
    """Return non-secret Sentinel/Node/cache diagnostics for administrators."""
    try:
        from sentinel_quickjs import sentinel_runtime_status
        return {"ok": True, **sentinel_runtime_status()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:240]}


@app.post("/api/auth/login")
def api_login(req: LoginReq):
    user = db.authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(401, "Invalid username or password")
    token, csrf = db.create_session(user["id"])
    response = JSONResponse({"ok": True, "user": user, "csrf_token": csrf})
    response.set_cookie(AUTH_COOKIE, token, httponly=True, secure=_secure_cookies(), samesite="lax",
                        max_age=7 * 86400, path="/")
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, secure=_secure_cookies(), samesite="lax",
                        max_age=7 * 86400, path="/")
    return response


@app.get("/api/auth/me")
def api_me(request: Request):
    return {"ok": True, "user": request.state.user}


@app.post("/api/auth/logout")
def api_logout(request: Request):
    token = request.cookies.get(AUTH_COOKIE, "")
    db.revoke_session(token)
    response = JSONResponse({"ok": True})
    response.delete_cookie(AUTH_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return response


@app.get("/api/admin/users")
def api_list_users():
    return {"ok": True, "users": db.list_users()}


@app.post("/api/admin/users")
def api_create_user(req: CreateUserReq):
    try:
        return {"ok": True, "user": db.create_user(req.username, req.password, req.role)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.patch("/api/admin/users/{user_id}")
def api_update_user(user_id: int, req: UpdateUserReq):
    try:
        return {"ok": True, "user": db.update_user(user_id, role=req.role, password=req.password, is_active=req.is_active)}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/admin/users/{user_id}")
def api_delete_user(user_id: int):
    try:
        db.delete_user(user_id)
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/import")
def api_import(req: ImportReq):
    result = db.import_accounts(req.text)
    return {"ok": True, **result, "stats": db.stats()}


@app.get("/api/accounts")
def api_accounts(status: str = "", limit: int = 50, offset: int = 0):
    items = db.list_accounts(status=status, limit=limit, offset=offset)
    total = db.count_accounts(status=status)
    return {"ok": True, "items": items, "total": total}


class ImapImportReq(BaseModel):
    text: str


@app.post("/api/imap_accounts/import")
def api_import_imap_accounts(req: ImapImportReq):
    return {"ok": True, **db.import_imap_accounts(req.text)}


@app.get("/api/imap_accounts")
def api_imap_accounts(status: str = "", limit: int = 50, offset: int = 0):
    return {"ok": True, "items": db.list_imap_accounts(status, limit, offset), "total": db.count_imap_accounts(status)}


class ImapPoolActionReq(BaseModel):
    emails: Optional[list[str]] = None
    status: Optional[str] = None


@app.post("/api/imap_accounts/reset")
def api_reset_imap_accounts(req: ImapPoolActionReq):
    return {"ok": True, "reset": db.reset_imap_accounts(req.emails, req.status or "")}


@app.delete("/api/imap_accounts/{email}")
def api_delete_imap_account(email: str):
    if not db.delete_imap_accounts([email]):
        raise HTTPException(404, "not found")
    return {"ok": True}


@app.post("/api/imap_accounts/bulk_delete")
def api_bulk_delete_imap_accounts(req: ImapPoolActionReq):
    return {"ok": True, "deleted": db.delete_imap_accounts(req.emails, req.status or "")}


@app.delete("/api/accounts/{email}")
def api_delete_account(email: str):
    ok = db.delete_account(email)
    if not ok:
        raise HTTPException(404, "not found")
    return {"ok": True}


class BulkDeleteReq(BaseModel):
    status: Optional[str] = Field(None, description="available/in_use/done/failed/all")
    emails: Optional[list[str]] = Field(None, description="按 email 列表删")


@app.post("/api/accounts/bulk_delete")
def api_bulk_delete(req: BulkDeleteReq):
    """按状态或 email 列表批量删除号池。两个参数二选一（status 优先）。"""
    if req.status:
        n = db.delete_accounts_by_status(req.status)
        return {"ok": True, "deleted": n, "by": "status", "stats": db.stats()}
    if req.emails:
        n = db.delete_accounts_by_emails(req.emails)
        return {"ok": True, "deleted": n, "by": "emails", "stats": db.stats()}
    raise HTTPException(400, "需要 status 或 emails")


@app.post("/api/accounts/reset_failed")
def api_reset_failed():
    n = db.reset_failed_to_available()
    return {"ok": True, "reset": n, "stats": db.stats()}


@app.post("/api/accounts/reset/{email}")
def api_reset_account(email: str):
    """重置单个号：done / failed → available。"""
    ok = db.reset_to_available(email)
    if not ok:
        raise HTTPException(404, f"邮箱 {email} 不存在")
    return {"ok": True, "email": email}


class BulkResetReq(BaseModel):
    emails: list[str]


@app.post("/api/accounts/bulk_reset")
def api_bulk_reset(req: BulkResetReq):
    """批量重置：done / failed → available。"""
    if not req.emails:
        raise HTTPException(400, "emails 不能为空")
    n = db.bulk_reset_to_available(req.emails)
    return {"ok": True, "reset": n, "stats": db.stats()}


@app.post("/api/accounts/release_stale")
def api_release_stale(stale_seconds: int = 1800):
    n = db.release_stale_in_use(stale_seconds=stale_seconds)
    return {"ok": True, "released": n, "stats": db.stats()}


@app.get("/api/stats")
def api_stats():
    return {"ok": True, "stats": db.stats()}


@app.get("/api/dashboard/sms_balance")
def api_dashboard_sms_balance():
    """Return the live balance for the configured SMS provider."""
    cfg = db.get_sms_internal_config()
    provider_key = str(cfg.get("sms_provider") or "smsbower")
    checked_at = datetime.now(timezone.utc).isoformat()
    if not cfg.get("sms_api_key"):
        return {
            "ok": True,
            "configured": False,
            "provider": provider_key,
            "balance": None,
            "checked_at": checked_at,
        }

    try:
        from sms_provider import create_sms_provider
        provider = create_sms_provider(provider_key, cfg)
        balance = provider.get_balance()
        return {
            "ok": True,
            "configured": True,
            "provider": provider_key,
            "balance": balance,
            "currency": None,
            "checked_at": checked_at,
        }
    except Exception as exc:
        logging.getLogger("webui").warning(
            "SMS balance check failed provider=%s error=%s", provider_key, str(exc)[:200]
        )
        return {
            "ok": False,
            "configured": True,
            "provider": provider_key,
            "balance": None,
            "checked_at": checked_at,
            "error": str(exc)[:200],
        }


# ──────────────────────── 代理连通性测试 ────────────────────────


class ProxyTestReq(BaseModel):
    proxies: list[str] = Field(..., description="要测试的代理列表")
    timeout: int = Field(8, description="每个代理超时秒数")
    test_url: str = Field("https://api.ipify.org?format=json",
                          description="测试目标 URL（默认返回出口 IP）")


@app.post("/api/proxy/test")
def api_proxy_test(req: ProxyTestReq):
    """并发测试代理连通性。复用真实注册流程的 create_http_session（含 socks5->socks5h
    标准化、trust_env=False），保证「测试正常」== 「跑号能用」。返回 ok / 延迟 / 出口 IP。

    协议说明：不写协议的 `ip:port` 被 curl 按 HTTP 代理处理；SOCKS5 需显式写 socks5://。
    """
    import sys as _sys
    ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(ROOT_DIR) not in _sys.path:
        _sys.path.insert(0, str(ROOT_DIR))
    try:
        from http_client import create_http_session
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"加载 http_client 失败: {e}")

    import time as _t
    from concurrent.futures import ThreadPoolExecutor

    timeout = max(1, min(int(req.timeout or 8), 60))
    test_url = (req.test_url or "https://api.ipify.org?format=json").strip()

    proxies = [p.strip() for p in (req.proxies or []) if p and p.strip()]
    if not proxies:
        raise HTTPException(400, "proxies 不能为空")

    def _test_one(proxy: str):
        t0 = _t.perf_counter()
        try:
            sess = create_http_session(proxy=proxy)
            resp = sess.get(test_url, timeout=timeout)
            latency = int((_t.perf_counter() - t0) * 1000)
            if resp.status_code != 200:
                return {"ok": False, "latency_ms": latency, "error": f"HTTP {resp.status_code}"}
            ip = ""
            try:
                ip = resp.json().get("ip", "")
            except Exception:
                ip = (resp.text or "").strip()[:64]
            return {"ok": True, "latency_ms": latency, "ip": ip}
        except Exception as e:  # noqa: BLE001
            latency = int((_t.perf_counter() - t0) * 1000)
            return {"ok": False, "latency_ms": latency, "error": str(e)[:140]}

    results = {}
    with ThreadPoolExecutor(max_workers=min(20, len(proxies))) as ex:
        for proxy, res in zip(proxies, ex.map(_test_one, proxies)):
            results[proxy] = res
    return {"ok": True, "results": results}


@app.post("/api/register")
def api_register(req: RegisterReq):
    """启动注册任务，返回 run_id。前端拿 run_id 去 /api/runs/{run_id}/stream 订阅 SSE。"""
    mail_source = db.get_setting("mail_source", "outlook")
    uses_catch_all = mail_source in ("cf_temp", "imap")

    if uses_catch_all:
        # Catch-all 模式：不需要 Outlook 号池，用虚拟占位 account。
        import time as _t
        account = {
            "email": f"{mail_source}_placeholder_{int(_t.time())}@local",
            "password": "",
            "client_id": "",
            "refresh_token": "",
        }
    elif mail_source == "imap_pool":
        account = db.claim_imap_account(req.email or "")
        if not account:
            raise HTTPException(400, "IMAP mailbox pool tidak memiliki mailbox available")
    elif req.email:
        account = db.claim_account(req.email)
        if not account:
            raise HTTPException(400, f"邮箱 {req.email} 不可用 (不存在 / 已 in_use / 已完成)")
    else:
        account = db.claim_next()
        if not account:
            raise HTTPException(400, "号池里没有 available 账号；请先批量导入")

    options = {
        "want_access_token": req.want_access_token,
        "want_session_token": req.want_session_token,
        "want_refresh_token": req.want_refresh_token,
        "proxy": req.proxy,
        "use_direct_connection": req.use_direct_connection,
        "otp_timeout": int(req.otp_timeout),
        "allow_existing_login": req.allow_existing_login,
    }
    run_id = registrar.start_registration(account, options)
    logger.info(f"[run] {run_id} -> {account['email']} (mail_source={mail_source})")
    return {"ok": True, "run_id": run_id, "email": account["email"]}


@app.get("/api/runs/{run_id}/stream")
async def api_stream(run_id: str, request: Request):
    """SSE 实时推送日志 + 事件。"""
    q = registrar.get_run_queue(run_id)
    if q is None:
        raise HTTPException(404, "run_id not found or finished")

    async def event_gen():
        loop = asyncio.get_event_loop()
        try:
            while True:
                if await request.is_disconnected():
                    break
                # 从队列取消息（用 run_in_executor 避免阻塞 event loop）
                msg = await loop.run_in_executor(None, _safe_get, q)
                if msg is None:
                    # sentinel: 任务结束
                    yield "event: end\ndata: {}\n\n"
                    break
                if msg.startswith("__EVENT__:"):
                    yield f"event: status\ndata: {msg[len('__EVENT__:'):]}\n\n"
                else:
                    yield f"event: log\ndata: {json.dumps({'line': msg}, ensure_ascii=False)}\n\n"
        finally:
            registrar.remove_run_queue(run_id)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 避免 nginx 缓冲
            "Connection": "keep-alive",
        },
    )


def _safe_get(q):
    try:
        return q.get(timeout=60)
    except Exception:
        return ""  # 心跳：返空串让 SSE 检查 disconnect


@app.get("/api/runs")
def api_runs(limit: int = 50):
    return {"ok": True, "items": db.list_runs(limit=limit)}


@app.get("/api/registered")
def api_registered(limit: int = 20, offset: int = 0, filter: str = "all"):
    items = db.list_registered(limit=limit, offset=offset, filter_rt=filter)
    total = db.count_registered(filter_rt=filter)
    return {"ok": True, "items": items, "total": total}


@app.get("/api/registered/{email}")
def api_registered_one(email: str):
    row = db.get_registered(email)
    if not row:
        raise HTTPException(404, "not found")
    return {"ok": True, "data": row}


@app.get("/api/registered/{email}/emails")
def api_registered_emails(email: str, limit: int = 10):
    """Fetch recent messages without persisting mailbox contents."""
    email = email.strip().lower()
    registered = db.get_registered(email)
    if not registered:
        raise HTTPException(404, "Registered account was not found")
    limit = max(1, min(int(limit or 10), 10))
    source = registered.get("source") or db.get_setting("mail_source", "outlook")
    try:
        from .email_reader import fetch_cloudflare_messages, fetch_imap_messages, fetch_outlook_messages

        if source == "imap":
            cfg = db.get_imap_credentials()
            required = ("host", "username", "password")
            if not all(cfg.get(key) for key in required):
                raise HTTPException(400, "IMAP mailbox configuration is incomplete")
            messages = fetch_imap_messages(cfg["host"], cfg["username"], cfg["password"], email, int(cfg["port"] or 993), limit)
        elif source == "imap_pool":
            account = db.get_imap_account(email)
            if not account:
                raise HTTPException(400, "The IMAP Pool mailbox was not found")
            messages = fetch_imap_messages(account["host"], account["email"], account["password"], email, int(account.get("port") or 993), limit)
        elif source == "cf_temp":
            cfg = db.get_mail_config()
            if not cfg.get("cf_api_url") or not cfg.get("cf_domain") or not db.get_cf_admin_token():
                raise HTTPException(400, "Cloudflare Mail configuration is incomplete")
            from mail_cf import CFTempEmailProvider
            provider = CFTempEmailProvider(cfg["cf_api_url"], db.get_cf_admin_token(), cfg["cf_domain"])
            messages = fetch_cloudflare_messages(provider, email, limit)
        else:
            account = db.get_account(email)
            if not account:
                raise HTTPException(400, "The Outlook mailbox was not found")
            messages = fetch_outlook_messages(
                email,
                registered.get("password") or account.get("password", ""),
                account.get("refresh_token", ""),
                account.get("client_id", ""),
                limit,
            )
        return {"ok": True, "email": email, "source": source, "messages": messages[:limit]}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Email fetch failed for %s (%s): %s", email, source, type(exc).__name__)
        raise HTTPException(502, "Unable to fetch recent emails from the mailbox") from exc


@app.delete("/api/registered/{email}")
def api_delete_registered(email: str):
    ok = db.delete_registered(email)
    if not ok:
        raise HTTPException(404, "not found")
    return {"ok": True}


class BulkDeleteRegisteredReq(BaseModel):
    emails: Optional[list[str]] = Field(None, description="按 email 列表删；留空 + all=true 则删全部")
    all: bool = False


class RecoverRefreshTokenReq(BaseModel):
    email: str
    proxy: str = ""
    use_direct_connection: bool = False
    otp_timeout: int = 10


class RecoverRefreshTokenBatchReq(BaseModel):
    emails: list[str]
    proxy: str = ""
    use_direct_connection: bool = False
    otp_timeout: int = 10


@app.post("/api/registered/bulk_delete")
def api_bulk_delete_registered(req: BulkDeleteRegisteredReq):
    if req.all:
        n = db.delete_all_registered()
        return {"ok": True, "deleted": n, "by": "all"}
    if req.emails:
        n = db.delete_registered_by_emails(req.emails)
        return {"ok": True, "deleted": n, "by": "emails"}
    raise HTTPException(400, "需要 emails 或 all=true")


@app.post("/api/registered/recover_refresh_token")
def api_recover_refresh_token(req: RecoverRefreshTokenReq):
    email = req.email.strip().lower()
    registered = db.get_registered(email)
    if not registered:
        raise HTTPException(404, "注册结果不存在")
    if registered.get("refresh_token"):
        raise HTTPException(400, "该账号已有 refresh_token")
    mail_source = registered.get("source") or db.get_setting("mail_source", "outlook")
    account = db.get_imap_account(email) if mail_source == "imap_pool" else db.get_account(email)
    if mail_source not in ("imap", "imap_pool") and not account:
        raise HTTPException(400, "邮箱池中找不到该账号，无法重新登录获取 RT")
    # IMAP catch-all registrations may not persist a password. AuthFlow derives
    # the same default password from the email for an existing-account login.
    if mail_source not in ("imap", "imap_pool") and not (registered.get("password") or (account or {}).get("password")):
        raise HTTPException(400, "该账号没有保存密码，无法重新登录获取 RT")
    if mail_source == "imap":
        imap = db.get_imap_credentials()
        missing = [key for key in ("host", "username", "password", "domain") if not imap[key]]
        if missing:
            raise HTTPException(400, "IMAP belum lengkap: " + ", ".join(missing))
    run_id = registrar.start_refresh_token_recovery(registered, account, {
        "proxy": req.proxy,
        "use_direct_connection": req.use_direct_connection,
        "otp_timeout": req.otp_timeout,
        "mail_source": mail_source,
    })
    return {"ok": True, "run_id": run_id, "email": email}


@app.post("/api/registered/recover_refresh_token_batch")
def api_recover_refresh_token_batch(req: RecoverRefreshTokenBatchReq):
    entries = []
    for raw_email in dict.fromkeys(req.emails):
        email = raw_email.strip().lower()
        registered = db.get_registered(email)
        if not registered or registered.get("refresh_token"):
            continue
        mail_source = registered.get("source") or db.get_setting("mail_source", "outlook")
        account = db.get_imap_account(email) if mail_source == "imap_pool" else db.get_account(email)
        if mail_source not in ("imap", "imap_pool") and not account:
            continue
        if mail_source not in ("imap", "imap_pool") and not (registered.get("password") or (account or {}).get("password")):
            continue
        if mail_source == "imap":
            imap = db.get_imap_credentials()
            if not all(imap[key] for key in ("host", "username", "password", "domain")):
                raise HTTPException(400, "IMAP belum lengkap")
        entries.append({"registered": registered, "account": account, "mail_source": mail_source})
    if not entries:
        raise HTTPException(400, "没有可获取 RT 的选中账号")
    run_id = registrar.start_refresh_token_recovery_batch(entries, {
        "proxy": req.proxy, "use_direct_connection": req.use_direct_connection,
        "otp_timeout": req.otp_timeout,
    })
    return {"ok": True, "run_id": run_id, "count": len(entries)}


# ──────────────────────── 邮箱来源配置 ────────────────────────


@app.get("/api/settings/mail")
def api_get_mail_config():
    return {"ok": True, "config": db.get_mail_config()}


class SaveMailConfigReq(BaseModel):
    mail_source: Optional[str] = None       # outlook / cf_temp / imap
    cf_api_url: Optional[str] = None
    cf_admin_token: Optional[str] = None
    cf_domain: Optional[str] = None
    imap_host: Optional[str] = None
    imap_port: Optional[str] = None
    imap_username: Optional[str] = None
    imap_password: Optional[str] = None
    imap_domain: Optional[str] = None


@app.post("/api/settings/mail")
def api_save_mail_config(req: SaveMailConfigReq):
    db.save_mail_config(req.model_dump(exclude_none=True))
    return {"ok": True, "config": db.get_mail_config()}


@app.post("/api/settings/mail/test")
def api_test_mail():
    """Test the active catch-all mailbox configuration."""
    mail_source = db.get_setting("mail_source", "outlook")
    if mail_source == "imap":
        from mail_imap import ImapCatchAllProvider
        config = db.get_imap_credentials()
        missing = [name for name in ("host", "username", "password", "domain") if not config[name]]
        if missing:
            raise HTTPException(400, "未配置 IMAP: " + ", ".join(missing))
        try:
            provider = ImapCatchAllProvider(
                host=config["host"], username=config["username"], password=config["password"],
                domain=config["domain"], port=int(config["port"] or 993),
            )
            provider.test_connection()
            return {"ok": True, "message": "IMAP 登录成功"}
        except Exception as e:
            raise HTTPException(500, f"IMAP 连接失败: {e}")
    if mail_source != "cf_temp":
        raise HTTPException(400, f"当前 mail_source={mail_source}，不需要测试")

    api_url = db.get_setting("cf_api_url", "")
    domain = db.get_setting("cf_domain", "")
    token = db.get_cf_admin_token()
    if not api_url:
        raise HTTPException(400, "未配置 cf_api_url")
    if not domain:
        raise HTTPException(400, "未配置 cf_domain")
    if not token:
        raise HTTPException(400, "未配置 cf_admin_token")

    import sys as _sys
    ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(ROOT_DIR) not in _sys.path:
        _sys.path.insert(0, str(ROOT_DIR))
    from mail_cf import CFTempEmailProvider
    try:
        provider = CFTempEmailProvider(api_url=api_url, admin_token=token, domain=domain)
        test_email = provider.create_mailbox()
        return {"ok": True, "message": f"连接成功，测试邮箱: {test_email}"}
    except Exception as e:
        raise HTTPException(500, f"连接失败: {e}")


# ──────────────────────── SMS 接码配置 ────────────────────────


@app.get("/api/settings/sms")
def api_get_sms_config():
    return {"ok": True, "config": db.get_sms_config()}


class SaveSmsConfigReq(BaseModel):
    sms_enabled: Optional[str] = None              # "0" / "1"
    sms_provider: Optional[str] = None             # smsbower / herosms / 5sim
    sms_api_key: Optional[str] = None              # 传 '***' 表示不修改
    sms_country: Optional[str] = None              # ID 或 5sim country code
    sms_service: Optional[str] = None              # OpenAI = 'dr' or 'openai'
    sms_max_price: Optional[str] = None
    sms_reuse_phone: Optional[str] = None
    sms_phone_success_max: Optional[str] = None
    sms_auto_country: Optional[str] = None
    sms_strict_whitelist: Optional[str] = None
    sms_allowed_countries: Optional[str] = None    # 逗号分隔的 ID 列表，自动选号时只从这里挑
    sms_auto_min_stock: Optional[str] = None
    sms_auto_max_price: Optional[str] = None
    sms_max_phone_attempts: Optional[str] = None   # 空 = 用 provider 默认；>0 = 自定义
    sms_per_phone_timeout: Optional[str] = None    # 单号等待秒数（默认 80）


@app.post("/api/settings/sms")
def api_save_sms_config(req: SaveSmsConfigReq):
    db.save_sms_config(req.model_dump(exclude_none=True))
    return {"ok": True, "config": db.get_sms_config()}


@app.post("/api/settings/sms/test")
def api_test_sms():
    """测试 SMS provider 连通性：查询余额。"""
    cfg = db.get_sms_internal_config()
    if not cfg.get("sms_api_key"):
        raise HTTPException(400, "未配置 sms_api_key")

    import sys as _sys
    ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(ROOT_DIR) not in _sys.path:
        _sys.path.insert(0, str(ROOT_DIR))
    from sms_provider import create_sms_provider
    try:
        provider = create_sms_provider(cfg["sms_provider"], cfg)
        balance = provider.get_balance()
        return {
            "ok": True,
            "provider": cfg["sms_provider"],
            "balance": balance,
            "message": f"连接成功，余额: {balance}",
        }
    except Exception as e:
        raise HTTPException(500, f"连接失败: {e}")


@app.get("/api/settings/sms/countries")
def api_sms_top_countries():
    """查询当前接码平台的国家排名（价格 + 库存）。"""
    cfg = db.get_sms_internal_config()
    if not cfg.get("sms_api_key"):
        raise HTTPException(400, "未配置 sms_api_key")

    import sys as _sys
    ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(ROOT_DIR) not in _sys.path:
        _sys.path.insert(0, str(ROOT_DIR))
    from sms_provider import create_sms_provider, OPENAI_SMS_COUNTRIES, SMS_COUNTRY_NAMES_CN
    try:
        provider = create_sms_provider(cfg["sms_provider"], cfg)
        service = "openai" if cfg["sms_provider"] == "5sim" else (cfg.get("sms_service") or "dr")
        rows = provider.get_top_countries(service=service)
        for r in rows:
            cid = str(r.get("country"))
            r["openai_sms_safe"] = cid in OPENAI_SMS_COUNTRIES
            r["name_cn"] = SMS_COUNTRY_NAMES_CN.get(cid, "未知")
        return {"ok": True, "countries": rows[:30], "openai_sms_safe": list(OPENAI_SMS_COUNTRIES)}
    except Exception as e:
        raise HTTPException(500, f"查询失败: {e}")


@app.get("/api/settings/sms/all_countries")
def api_sms_all_countries(provider: str = ""):
    """返回当前平台实际有库存的国家（动态查询）；查询失败则 fallback 到静态字典。"""
    import sys as _sys
    ROOT_DIR = Path(__file__).resolve().parents[1]
    if str(ROOT_DIR) not in _sys.path:
        _sys.path.insert(0, str(ROOT_DIR))
    from sms_provider import SMS_COUNTRY_NAMES_CN, OPENAI_SMS_COUNTRIES, create_sms_provider

    cfg = db.get_sms_internal_config()
    if provider:
        cfg["sms_provider"] = provider

    # 尝试从平台 API 动态获取有库存的国家
    if cfg.get("sms_api_key"):
        try:
            p = create_sms_provider(cfg["sms_provider"], cfg)
            if cfg["sms_provider"] == "5sim" and hasattr(p, "get_country_options"):
                countries = []
                for r in p.get_country_options():
                    cid = str(r.get("country") or "")
                    countries.append({"id": cid, "name_cn": cid, "openai_sms_safe": False,
                                      "price": r.get("price"), "count": r.get("count")})
                return {"ok": True, "countries": countries, "openai_sms_safe": [], "source": "5sim"}
            service = "openai" if cfg["sms_provider"] == "5sim" else (cfg.get("sms_service") or "dr")
            rows = p.get_top_countries(service=service)
            countries = []
            for r in rows:
                cid = str(r.get("country") or "")
                countries.append({
                    "id": cid,
                    "name_cn": SMS_COUNTRY_NAMES_CN.get(cid, f"国家{cid}"),
                    "openai_sms_safe": cid in OPENAI_SMS_COUNTRIES,
                    "price": r.get("price"),
                    "count": r.get("count"),
                })
            if countries:
                return {"ok": True, "countries": countries,
                        "openai_sms_safe": list(OPENAI_SMS_COUNTRIES), "source": "live"}
        except Exception:
            pass

    # fallback: 静态字典
    items = sorted(SMS_COUNTRY_NAMES_CN.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 9999)
    countries = [
        {"id": cid, "name_cn": name, "openai_sms_safe": cid in OPENAI_SMS_COUNTRIES}
        for cid, name in items
    ]
    return {"ok": True, "countries": countries,
            "openai_sms_safe": list(OPENAI_SMS_COUNTRIES), "source": "static"}


# ──────────────────────── 自动导出 (CPA / SUB2API) ────────────────────────


class SaveExportConfigReq(BaseModel):
    # CPA
    cpa_enabled: Optional[str] = None       # "0" / "1"
    cpa_url: Optional[str] = None
    cpa_mgmt_key: Optional[str] = None      # 传 '***' 表示不修改
    cpa_timeout: Optional[str] = None
    # SUB2API
    sub2api_enabled: Optional[str] = None
    sub2api_url: Optional[str] = None
    sub2api_api_key: Optional[str] = None   # '***' 不修改
    sub2api_group_ids: Optional[str] = None  # 逗号分隔，例 "2" 或 "1,2,3"
    sub2api_timeout: Optional[str] = None


@app.get("/api/settings/export")
def api_get_export_config():
    return {"ok": True, "config": db.get_export_config()}


@app.post("/api/settings/export")
def api_save_export_config(req: SaveExportConfigReq):
    db.save_export_config(req.model_dump(exclude_none=True))
    return {"ok": True, "config": db.get_export_config()}


class TestExportReq(BaseModel):
    target: str = Field(..., description="cpa 或 sub2api")


@app.post("/api/settings/export/test")
def api_test_export(req: TestExportReq):
    """测试 CPA / SUB2API 连通性。"""
    from . import exporter
    cfg = db.get_export_internal_config()
    target = (req.target or "").strip().lower()
    try:
        if target == "cpa":
            return exporter.test_cpa(cfg["cpa"])
        if target == "sub2api":
            return exporter.test_sub2api(cfg["sub2api"])
        raise HTTPException(400, f"未知 target: {target}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"测试失败: {e}")


class ManualExportReq(BaseModel):
    email: str = Field(..., description="要导出的已注册账号邮箱")
    targets: list[str] = Field(default_factory=lambda: ["cpa", "sub2api"],
                                description="选择导出目标：cpa / sub2api")


@app.post("/api/registered/export_to_panel")
def api_manual_export_to_panel(req: ManualExportReq):
    """对一个已注册账号手动触发到面板的导出。

    targets 里选 cpa / sub2api 之一或全部。即使总开关未启用，本接口也会执行
    （只要 URL/密钥 等基础配置已填）。
    """
    from . import exporter
    cred = db.get_registered(req.email)
    if not cred:
        raise HTTPException(404, f"未找到已注册账号: {req.email}")

    cfg = db.get_export_internal_config()
    out = {"email": req.email, "cpa": None, "sub2api": None}
    targets = {t.strip().lower() for t in (req.targets or []) if t}

    if "cpa" in targets:
        cpa_cfg = dict(cfg["cpa"])
        cpa_cfg["enabled"] = True  # 手动触发：强制启用
        try:
            out["cpa"] = exporter.export_to_cpa(cred, cpa_cfg)
        except Exception as e:
            out["cpa"] = {"ok": False, "error": str(e)}
    if "sub2api" in targets:
        sub2api_cfg = dict(cfg["sub2api"])
        sub2api_cfg["enabled"] = True
        try:
            out["sub2api"] = exporter.export_to_sub2api(cred, sub2api_cfg)
        except Exception as e:
            out["sub2api"] = {"ok": False, "error": str(e)}

    return {"ok": True, **out}


# ──────────────────────── Plus 试用检查 ────────────────────────


class CheckPlusReq(BaseModel):
    emails: list[str] = Field(..., description="要检查的邮箱列表")
    proxy: str = Field("", description="查询代理，留空直连")


@app.post("/api/registered/check_plus")
def api_check_plus(req: CheckPlusReq):
    """用 access_token 查询账号的 Plus 试用状态。"""
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        raise HTTPException(500, "curl_cffi 未安装")

    results = {}
    for email in req.emails:
        cred = db.get_registered(email)
        if not cred:
            results[email] = {"status": "not_found", "label": "未找到"}
            continue
        at = (cred.get("access_token") or "").strip()
        if not at:
            results[email] = {"status": "no_at", "label": "无AT"}
            continue
        try:
            proxies = None
            proxy = req.proxy.strip()
            if proxy:
                proxies = {"https": proxy, "http": proxy}
            resp = cffi_requests.get(
                "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27",
                headers={
                    "Authorization": f"Bearer {at}",
                    "Accept": "application/json",
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/145.0.0.0 Safari/537.36"
                    ),
                },
                proxies=proxies,
                impersonate="chrome110",
                timeout=15,
            )
            if resp.status_code == 401:
                results[email] = {"status": "banned", "label": "封号"}
                continue
            if resp.status_code != 200:
                # HTTP 非 200/401 不记录，让前端继续显示"未检测"
                continue
            data = resp.json()
            accts = data.get("accounts", {})
            if not accts:
                # 无账户数据不记录，让前端继续显示"未检测"
                continue
            info = next(iter(accts.values()))
            acct = info.get("account", {})
            ent = info.get("entitlement", {})
            promo = info.get("eligible_promo_campaigns", {})
            is_deactivated = acct.get("is_deactivated", False)
            if is_deactivated:
                results[email] = {"status": "banned", "label": "封号"}
                continue
            plan = acct.get("plan_type", "free")
            has_sub = ent.get("has_active_subscription", False)
            has_plus_promo = "plus" in promo and promo["plus"].get("id") == "plus-1-month-free"
            if plan == "plus" or has_sub:
                results[email] = {"status": "plus_active", "label": "Plus生效中"}
            elif has_plus_promo:
                results[email] = {"status": "plus_eligible", "label": "可领Plus试用"}
            else:
                results[email] = {"status": "free", "label": "Free"}
        except Exception as e:
            # 所有异常（包括 curl 网络错误）都不记录，让前端继续显示"未检测"
            pass

    import time as _time
    checked_at = _time.time()
    for email, info in results.items():
        if info["status"] not in ("not_found", "no_at"):
            db.update_plus_check(email, {**info, "checked_at": checked_at})

    return {"ok": True, "results": results}


# ──────────────────────── auto-loop ────────────────────────


class AutoLoopStartReq(BaseModel):
    """跟 RegisterReq 复用同样的字段，auto-loop 内部传给每个 run。"""
    want_access_token: bool = True
    want_session_token: bool = True
    want_refresh_token: bool = True
    proxy: str = ""              # 单代理（concurrency=1 + 无代理池时用）
    proxy_pool: str = ""         # 多代理池（每行一个）；优先于 proxy
    use_direct_connection: bool = False
    concurrency: int = 1         # 并发 worker 数（1-20）
    otp_timeout: int = 10
    allow_existing_login: bool = True
    cool_down_seconds: float = 3.0  # 每个 worker 跑完后冷却（防风控）
    target_count: int = 0        # 目标成功数（0=不限量，达标自动停止）


@app.post("/api/auto/start")
def api_auto_start(req: AutoLoopStartReq):
    res = AUTO_LOOP.start(req.model_dump())
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "启动失败"))
    return res


@app.post("/api/auto/pause")
def api_auto_pause():
    res = AUTO_LOOP.pause()
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "暂停失败"))
    return res


@app.post("/api/auto/resume")
def api_auto_resume():
    res = AUTO_LOOP.resume()
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "恢复失败"))
    return res


@app.post("/api/auto/stop")
def api_auto_stop():
    res = AUTO_LOOP.stop()
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "停止失败"))
    return res


@app.get("/api/auto/status")
def api_auto_status():
    return {"ok": True, **AUTO_LOOP.status()}


@app.get("/api/auto/stream")
async def api_auto_stream(request: Request):
    """SSE 推送 auto-loop 状态变化 + run_started / run_finished 事件。"""
    q = AUTO_LOOP.subscribe()

    async def gen():
        loop = asyncio.get_event_loop()
        try:
            while True:
                if await request.is_disconnected():
                    break
                # 阻塞拿消息，但每 30s 心跳
                try:
                    msg = await loop.run_in_executor(None, lambda: q.get(timeout=30))
                except Exception:
                    yield ": heartbeat\n\n"
                    continue
                if msg is None:
                    break
                kind = msg.get("kind", "state")
                data = msg.get("data", {})
                yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        finally:
            AUTO_LOOP.unsubscribe(q)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ──────────────────────── 静态资源 ────────────────────────


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("webui.app:app", host="127.0.0.1", port=8765, reload=False)
