import tempfile
import threading
import unittest
from pathlib import Path
import sqlite3
from unittest.mock import patch

from mail_imap import _recipient_addresses
from mail_providers import MailProviderError, get_provider_class, list_pooled_providers, parse_import_line
from webui import db


class ICloudImapProviderTests(unittest.TestCase):
    def test_provider_is_registered_alongside_icloud_relay(self):
        self.assertEqual(get_provider_class("icloud_imap").kind, "icloud_imap")
        self.assertEqual(get_provider_class("icloud_relay").kind, "icloud_relay")
        pooled = {provider["kind"] for provider in list_pooled_providers()}
        self.assertIn("icloud_imap", pooled)
        self.assertIn("icloud_relay", pooled)

    def test_import_line_stores_imap_mailbox_email(self):
        row = parse_import_line(
            "alias@icloud.com----shared@example.com",
            "icloud_imap",
        )
        self.assertEqual(
            row,
            {
                "email": "alias@icloud.com",
                "kind": "icloud_imap",
                "imap_email": "shared@example.com",
            },
        )

    def test_import_line_rejects_non_email_as_imap_mailbox(self):
        with self.assertRaises(ValueError):
            parse_import_line(
                "alias@icloud.com----imap.example.com",
                "icloud_imap",
            )

    def test_import_persists_imap_email_without_changing_relay_storage(self):
        previous_path = db.DB_PATH
        previous_connections = db._connections
        with tempfile.TemporaryDirectory() as directory:
            db.DB_PATH = Path(directory) / "webui.db"
            db._connections = threading.local()
            try:
                db.init_db()
                db.import_accounts(
                    "imap-alias@icloud.com----shared@example.com",
                    kind="icloud_imap",
                )
                db.import_accounts(
                    "relay-alias@icloud.com----https://relay.example/messages/token",
                    kind="icloud_relay",
                )

                imap_row = db.get_account("imap-alias@icloud.com")
                relay_row = db.get_account("relay-alias@icloud.com")
                self.assertEqual(imap_row["kind"], "icloud_imap")
                self.assertEqual(imap_row["imap_email"], "shared@example.com")
                self.assertEqual(imap_row["relay_url"], "")
                self.assertEqual(relay_row["kind"], "icloud_relay")
                self.assertEqual(relay_row["relay_url"], "https://relay.example/messages/token")
                self.assertEqual(relay_row["imap_email"], "")
            finally:
                connection = getattr(db._connections, "connection", None)
                if connection is not None:
                    connection.close()
                db.DB_PATH = previous_path
                db._connections = previous_connections

    def test_from_config_uses_host_from_account_and_credentials_from_settings(self):
        with patch("mail_providers.icloud_imap.ImapCatchAllProvider") as imap_cls:
            provider = get_provider_class("icloud_imap").from_config(
                {
                    "icloud_imap_host": "imap.example.com",
                    "icloud_imap_username": "imap-login@example.com",
                    "icloud_imap_password": "app-password",
                    "icloud_imap_port": "1993",
                },
                {
                    "email": "alias@icloud.com",
                    "imap_email": "shared@example.com",
                },
            )

            imap_cls.assert_called_once_with(
                host="imap.example.com",
                username="imap-login@example.com",
                password="app-password",
                domain="icloud.com",
                port=1993,
                mailbox_email="shared@example.com",
            )
            self.assertEqual(provider.create_mailbox(), "alias@icloud.com")

            provider.wait_for_otp("alias@icloud.com", timeout=12, issued_after=123)
            imap_cls.return_value.wait_for_otp.assert_called_once_with(
                "shared@example.com",
                timeout=12,
                issued_after=123,
                additional_targets=("alias@icloud.com",),
            )

    def test_from_config_rejects_missing_shared_credentials(self):
        with self.assertRaises(MailProviderError):
            get_provider_class("icloud_imap").from_config(
                {},
                {"email": "alias@icloud.com", "imap_email": "shared@example.com"},
            )

    def test_old_database_is_migrated_with_imap_email_column(self):
        previous_path = db.DB_PATH
        previous_connections = db._connections
        with tempfile.TemporaryDirectory() as directory:
            db.DB_PATH = Path(directory) / "webui.db"
            db._connections = threading.local()
            try:
                connection = sqlite3.connect(db.DB_PATH)
                connection.execute(
                    """
                    CREATE TABLE outlook_accounts (
                        email TEXT PRIMARY KEY,
                        password TEXT,
                        client_id TEXT,
                        refresh_token TEXT,
                        relay_url TEXT,
                        kind TEXT NOT NULL DEFAULT 'outlook',
                        status TEXT NOT NULL DEFAULT 'available',
                        imported_at REAL,
                        claimed_at REAL,
                        finished_at REAL,
                        fail_reason TEXT
                    )
                    """
                )
                connection.commit()
                connection.close()

                db.init_db()
                columns = {
                    row[1]
                    for row in db._conn().execute("PRAGMA table_info(outlook_accounts)")
                }
                self.assertIn("imap_email", columns)
            finally:
                connection = getattr(db._connections, "connection", None)
                if connection is not None:
                    connection.close()
                db.DB_PATH = previous_path
                db._connections = previous_connections

    def test_recipient_addresses_include_forwarding_headers(self):
        from email import message_from_string

        message = message_from_string(
            "To: alias@icloud.com\n"
            "Delivered-To: grok@deka.dev\n"
            "X-Forwarded-To: shared@example.com\n"
            "X-Forwarded-For: original@example.com shared@example.com\n"
            "\n"
        )
        self.assertEqual(
            _recipient_addresses(message),
            {
                "alias@icloud.com",
                "grok@deka.dev",
                "shared@example.com",
                "original@example.com",
            },
        )


if __name__ == "__main__":
    unittest.main()
