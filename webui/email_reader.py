"""Fetch recent mailbox messages for the registered-account viewer."""
from __future__ import annotations

import email
import email.header
import email.utils
import html
import imaplib
import re
from datetime import timezone
from typing import Any

from mail_outlook import _graph_list_messages, _request_access_token, GRAPH_SCOPE, IMAP_SERVERS


def _decode_header(value: str) -> str:
    parts = email.header.decode_header(value or "")
    out = []
    for part, charset in parts:
        if isinstance(part, bytes):
            out.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(str(part))
    return "".join(out).strip()


def _plain_text(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw or "")
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _message_text(message) -> str:
    chunks = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        if part.get_content_type() not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True) or b""
        chunks.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
    return _plain_text("\n".join(chunks))


def _normalized(subject: str, sender: str, recipients: list[str], received: str, body: str, message_id: str) -> dict:
    return {
        "id": message_id or f"{received}:{subject}:{sender}",
        "from": _decode_header(sender),
        "to": recipients,
        "subject": _decode_header(subject) or "(no subject)",
        "received_at": received,
        "preview": body[:240],
        "body": body,
    }


def _date_value(value: str) -> str:
    try:
        dt = email.utils.parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return value or ""


def fetch_imap_messages(host: str, username: str, password: str, mailbox: str, port: int = 993, limit: int = 10) -> list[dict]:
    client = imaplib.IMAP4_SSL(host, int(port), timeout=20)
    try:
        client.login(username, password)
        status, _ = client.select("INBOX", readonly=True)
        if status != "OK":
            raise RuntimeError("Unable to select the IMAP INBOX")
        status, data = client.uid("search", None, "ALL")
        if status != "OK":
            raise RuntimeError("Unable to search the IMAP INBOX")
        results = []
        for uid in reversed((data[0] or b"").split()[-max(10, limit * 3):]):
            status, raw = client.uid("fetch", uid, "(RFC822)")
            if status != "OK" or not raw or not raw[0]:
                continue
            message = email.message_from_bytes(raw[0][1])
            recipients = [addr for _, addr in email.utils.getaddresses([
                message.get("To", ""), message.get("Delivered-To", ""), message.get("X-Original-To", ""),
            ]) if addr]
            if mailbox and mailbox.lower() not in {addr.lower() for addr in recipients}:
                continue
            results.append(_normalized(
                message.get("Subject", ""), message.get("From", ""), recipients,
                _date_value(message.get("Date", "")), _message_text(message), uid.decode(errors="replace"),
            ))
            if len(results) >= limit:
                break
        return results
    finally:
        try:
            client.logout()
        except Exception:
            pass


def _graph_messages(email_addr: str, refresh_token: str, client_id: str, limit: int) -> list[dict]:
    token = _request_access_token(refresh_token, client_id, GRAPH_SCOPE)["access_token"]
    results = []
    for folder in ("inbox", "junkemail", "deleteditems"):
        for msg in _graph_list_messages(token, folder, timeout=15)[:limit]:
            sender = ((msg.get("from") or {}).get("emailAddress") or {}).get("address", "")
            recipients = [((row.get("emailAddress") or {}).get("address", "")) for row in (msg.get("toRecipients") or [])]
            body = _plain_text(((msg.get("body") or {}).get("content") or msg.get("bodyPreview") or ""))
            results.append(_normalized(
                msg.get("subject", ""), sender, recipients, msg.get("receivedDateTime", ""),
                body, f"graph:{folder}:{msg.get('id', '')}",
            ))
    results.sort(key=lambda row: row.get("received_at", ""), reverse=True)
    return results[:limit]


def fetch_outlook_messages(email_addr: str, password: str, refresh_token: str, client_id: str, limit: int = 10) -> list[dict]:
    graph_error = None
    if refresh_token and client_id:
        try:
            return _graph_messages(email_addr, refresh_token, client_id, limit)
        except Exception as exc:
            graph_error = exc
    if password:
        for host in IMAP_SERVERS:
            try:
                return fetch_imap_messages(host, email_addr, password, email_addr, limit=limit)
            except Exception:
                continue
    if graph_error:
        raise RuntimeError(f"Unable to read the Outlook mailbox: {type(graph_error).__name__}") from graph_error
    raise RuntimeError("No usable Outlook mailbox credentials are configured")


def fetch_cloudflare_messages(provider: Any, email_addr: str, limit: int = 10) -> list[dict]:
    messages = provider._get_mails(email_addr)
    results = []
    for item in messages:
        raw = str(item.get("raw") or item.get("body") or item.get("text") or "")
        results.append(_normalized(
            str(item.get("subject") or ""), str(item.get("from") or item.get("sender") or ""),
            [email_addr], str(item.get("date") or item.get("receivedAt") or ""),
            _plain_text(raw), str(item.get("id") or ""),
        ))
    return results[:limit]
