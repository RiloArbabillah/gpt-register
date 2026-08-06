"""IMAP catch-all mailbox provider for registration email verification codes."""
from __future__ import annotations

import email
import email.utils
import imaplib
import logging
import random
import re
import string
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_FROM_DOMAINS = ("openai.com", "auth.openai", "tm.openai", "chatgpt.com", "tm.open")


def _extract_otp(body: str) -> Optional[str]:
    for pattern in (
        r"(?:code(?:\s*is)?|verification|one[-\s]*time|verify|kode|verifikasi)[^\d<>]{0,80}(\d{6})\b",
        r"(?:chatgpt|openai)[^\d<>]{0,80}(\d{6})",
        r"\b(\d{6})\b",
    ):
        for match in re.finditer(pattern, body, re.IGNORECASE | re.DOTALL):
            code = match.group(1)
            before = body[max(0, match.start(1) - 30):match.start(1)]
            if not (match.start(1) > 0 and body[match.start(1) - 1] == "#") and not re.search(
                r"(?:color|background|bgcolor|fill|stroke)\s*[:=]\s*[\"']?#?\s*$", before, re.I
            ):
                return code
    return None


def _message_body(message: email.message.Message) -> str:
    parts = message.walk() if message.is_multipart() else [message]
    chunks = []
    for part in parts:
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            chunks.append(payload.decode(part.get_content_charset() or "utf-8", errors="replace"))
        except Exception:
            continue
    return "\n".join(chunks)


def _recipient_addresses(message: email.message.Message) -> set[str]:
    """Return normalized recipients, including common forwarding headers."""
    values = []
    for header in (
        "To",
        "Delivered-To",
        "X-Original-To",
        "X-Forwarded-To",
        "X-Forwarded-For",
    ):
        values.extend(message.get_all(header, []))
    try:
        parsed = email.utils.getaddresses(values, strict=False)
    except TypeError:  # Python versions before the strict argument
        parsed = email.utils.getaddresses(values)
    addresses = {
        address.lower()
        for _, address in parsed
        if address and "@" in address and not address.startswith("@")
    }
    # Python 3.14's strict parser can drop bare addresses in forwarding lists.
    for value in values:
        addresses.update(
            match.group(0).lower()
            for match in re.finditer(
                r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                value,
            )
        )
    return addresses


class ImapCatchAllProvider:
    """Generate unique catch-all addresses and retrieve their OTPs through IMAP."""

    def __init__(self, host: str, username: str, password: str, domain: str, port: int = 993, mailbox_email: str = ""):
        if not all((host, username, password, domain)):
            raise ValueError("IMAP host, username, password, and domain are required")
        self.host = host.strip()
        self.username = username.strip()
        self.password = password
        self.domain = domain.strip().lower().lstrip("@")
        self.port = int(port)
        self.last_persona = None
        self.catch_all_domain = self.domain
        self._rng = random.SystemRandom()
        self.mailbox_email = mailbox_email.strip().lower()

    def create_mailbox(self) -> str:
        if self.mailbox_email:
            logger.info("[imap] using pool mailbox: %s", self.mailbox_email)
            return self.mailbox_email
        local = "".join(self._rng.choices(string.ascii_lowercase + string.digits, k=12))
        address = f"{local}-gpt@{self.domain}"
        logger.info("[imap] using catch-all address: %s", address)
        return address

    def test_connection(self) -> None:
        client = imaplib.IMAP4_SSL(self.host, self.port, timeout=20)
        try:
            client.login(self.username, self.password)
            status, _ = client.select("INBOX", readonly=True)
            if status != "OK":
                raise RuntimeError("cannot select IMAP INBOX")
        finally:
            try:
                client.logout()
            except Exception:
                pass

    def wait_for_otp(
        self,
        email_addr: str,
        timeout: int = 120,
        issued_after: Optional[float] = None,
        additional_targets: Optional[set[str] | list[str] | tuple[str, ...]] = None,
    ) -> str:
        deadline = time.time() + max(10, int(timeout))
        # Date headers can precede actual delivery by several seconds due to
        # forwarding. Keep the window narrow enough to avoid old codes while
        # allowing a freshly delivered message whose Date is slightly stale.
        threshold = (issued_after or time.time()) - 60
        targets = {email_addr.lower()}
        targets.update(
            target.strip().lower()
            for target in (additional_targets or ())
            if target and target.strip()
        )
        logger.info("[imap] waiting for OTP -> %s", ", ".join(sorted(targets)))

        while time.time() < deadline:
            client = None
            try:
                client = imaplib.IMAP4_SSL(self.host, self.port, timeout=20)
                client.login(self.username, self.password)
                status, _ = client.select("INBOX", readonly=True)
                if status != "OK":
                    raise RuntimeError("cannot select IMAP INBOX")
                status, data = client.uid("search", None, "ALL")
                if status != "OK":
                    raise RuntimeError("cannot search IMAP INBOX")
                for uid in reversed((data[0] or b"").split()[-30:]):
                    status, raw = client.uid("fetch", uid, "(RFC822)")
                    if status != "OK" or not raw or not raw[0]:
                        continue
                    message = email.message_from_bytes(raw[0][1])
                    sender = message.get("From", "").lower()
                    if not any(domain in sender for domain in _FROM_DOMAINS) or "tm1.openai" in sender:
                        continue
                    recipients = _recipient_addresses(message)
                    if not targets.intersection(recipients):
                        continue
                    try:
                        received = email.utils.parsedate_to_datetime(message.get("Date", ""))
                        if received.tzinfo is None:
                            received = received.replace(tzinfo=timezone.utc)
                        if received.timestamp() < threshold:
                            continue
                    except Exception:
                        pass
                    otp = _extract_otp(_message_body(message))
                    if otp:
                        logger.info("[imap] OTP received for %s", ", ".join(sorted(targets)))
                        return otp
            except imaplib.IMAP4.error as exc:
                raise RuntimeError(f"IMAP authentication or protocol error: {exc}") from exc
            finally:
                if client is not None:
                    try:
                        client.logout()
                    except Exception:
                        pass
            time.sleep(4)
        raise TimeoutError(f"IMAP OTP timeout for {email_addr}")
