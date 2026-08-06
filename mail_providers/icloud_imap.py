"""iCloud alias provider backed by a shared IMAP mailbox."""
from __future__ import annotations

from typing import Optional

from mail_imap import ImapCatchAllProvider

from .base import ConfigField, MailProvider, MailProviderError, register, validate_email


@register
class ICloudImapProvider(MailProvider):
    """Read fixed iCloud aliases from a shared, authenticated IMAP inbox."""

    kind = "icloud_imap"
    display_name = "iCloud IMAP"
    pooled = True
    ephemeral = False
    accepts_existing_account = True

    line_segments = 2
    import_hint = "email iCloud----email mailbox IMAP"
    import_placeholder = "alias@icloud.com----shared@example.com"

    config_fields = [
        ConfigField(
            "icloud_imap_host",
            "IMAP host",
            placeholder="imap.example.com",
        ),
        ConfigField(
            "icloud_imap_username",
            "IMAP username",
            placeholder="mailbox@example.com",
        ),
        ConfigField(
            "icloud_imap_password",
            "IMAP password / app password",
            type="password",
        ),
        ConfigField(
            "icloud_imap_port",
            "IMAP port",
            type="number",
            required=False,
            placeholder="993",
        ),
    ]

    def __init__(
        self,
        email: str,
        imap_email: str,
        host: str,
        username: str,
        password: str,
        port: int = 993,
    ):
        email = (email or "").strip().lower()
        imap_email = (imap_email or "").strip().lower()
        host = (host or "").strip()
        if not email:
            raise ValueError("iCloud email address is required")
        validate_email(email)
        if not imap_email:
            raise ValueError("iCloud IMAP mailbox email is required")
        validate_email(imap_email)
        self._validate_host(host)
        if not username or not password:
            raise ValueError("iCloud IMAP username and password are required")
        try:
            port = int(port or 993)
        except (TypeError, ValueError) as exc:
            raise ValueError("iCloud IMAP port must be a number") from exc
        if not 1 <= port <= 65535:
            raise ValueError("iCloud IMAP port must be between 1 and 65535")

        self.email = email
        self.imap_email = imap_email
        self.imap_host = host
        self._dead = False
        self.last_persona = None
        self._imap = ImapCatchAllProvider(
            host=host,
            username=username,
            password=password,
            domain=email.rsplit("@", 1)[-1],
            port=port,
            mailbox_email=imap_email,
        )

    @staticmethod
    def _validate_host(host: str) -> None:
        if not host:
            raise ValueError("iCloud IMAP host is required")
        if any(ch.isspace() for ch in host) or "/" in host or "://" in host:
            raise ValueError("iCloud IMAP host must be a hostname or IP address")

    @classmethod
    def from_config(cls, settings: dict, account: Optional[dict] = None):
        if not account:
            raise MailProviderError(
                "iCloud IMAP is a mailbox pool: import email----IMAP host first",
                fatal=False,
                kind=cls.kind,
            )

        imap_email = (account.get("imap_email") or "").strip()
        host = (settings.get("icloud_imap_host") or "").strip()
        username = (settings.get("icloud_imap_username") or "").strip()
        password = settings.get("icloud_imap_password") or ""
        port = settings.get("icloud_imap_port") or 993
        missing = []
        if not imap_email:
            missing.append("IMAP mailbox email in the imported account")
        if not host:
            missing.append("icloud_imap_host")
        if not username:
            missing.append("icloud_imap_username")
        if not password:
            missing.append("icloud_imap_password")
        if missing:
            raise MailProviderError(
                "iCloud IMAP configuration is incomplete (missing "
                + ", ".join(missing)
                + ")",
                fatal=True,
                kind=cls.kind,
            )
        try:
            return cls(
                email=account.get("email", ""),
                imap_email=imap_email,
                host=host,
                username=username,
                password=password,
                port=port,
            )
        except ValueError as exc:
            raise MailProviderError(str(exc), fatal=True, kind=cls.kind) from exc

    @property
    def exhausted(self) -> bool:
        return self._dead

    def mark_dead(self, reason: str = "") -> None:
        self._dead = True

    def create_mailbox(self) -> str:
        return self.email

    def wait_for_otp(self, email_addr: str, timeout: int = 120, issued_after=None) -> str:
        return self._imap.wait_for_otp(
            self.imap_email,
            timeout=timeout,
            issued_after=issued_after,
            additional_targets=(self.email,),
        )

    @classmethod
    def parse_line(cls, line: str) -> dict:
        parts = [part.strip() for part in (line or "").split("----")]
        if len(parts) != 2:
            raise ValueError(
                f"需要 2 段（email----IMAP mailbox email），实际 {len(parts)} 段"
            )
        email, imap_email = parts
        validate_email(email)
        validate_email(imap_email)
        return {
            "email": email.lower(),
            "kind": cls.kind,
            "imap_email": imap_email.lower(),
        }
