"""Pure-helper tests for the autonomous-configuration module.

These never spawn a tunnel, never touch DNS, and never call Plivo. The
network-facing paths (quick tunnel, DNS wait, auto-wire) are exercised only
against a live account. Runs under both ``python -m unittest`` and pytest.
"""

import asyncio
import os
import unittest

from hermes_plivo_sms import setup


class EnvCase(unittest.TestCase):
    """Save and restore the Plivo-related environment around each test."""

    _VARS = (
        "PLIVO_SMS_PUBLIC_URL",
        "PLIVO_WEBHOOK_URL",
        "PLIVO_SMS_AUTO_WIRE",
        "PLIVO_AUTH_ID",
        "PLIVO_AUTH_TOKEN",
        "PLIVO_PHONE_NUMBER",
        "PLIVO_TEST_SECRET_XYZ",
    )

    def setUp(self):
        self._saved = {v: os.environ.get(v) for v in self._VARS}
        for v in self._VARS:
            os.environ.pop(v, None)

    def tearDown(self):
        for v, val in self._saved.items():
            if val is None:
                os.environ.pop(v, None)
            else:
                os.environ[v] = val


class SetupHelperTests(EnvCase):
    def test_normalize_base_variants(self):
        n = setup._normalize_base
        self.assertEqual(n("https://abc.trycloudflare.com"), "https://abc.trycloudflare.com")
        self.assertEqual(n("https://abc.trycloudflare.com/"), "https://abc.trycloudflare.com")
        self.assertEqual(n("example.com"), "https://example.com")
        self.assertEqual(n("https://example.com/webhooks/plivo"), "https://example.com")
        self.assertEqual(n("http://example.com:8443/x"), "https://example.com:8443")

    def test_wiring_constants(self):
        self.assertEqual(setup.MESSAGE_PATH, "/plivo/sms/message")
        self.assertEqual(setup.APP_NAME_PREFIX, "hermes-plivo-sms")

    def test_app_name_is_keyed_by_digits_only_number(self):
        self.assertEqual(setup.app_name_for("+14155551234"), "hermes-plivo-sms-14155551234")
        self.assertEqual(setup.app_name_for("14155551234"), "hermes-plivo-sms-14155551234")
        self.assertEqual(setup.app_name_for("+1 (415) 555-1234"), "hermes-plivo-sms-14155551234")

    def test_tunnel_url_regex(self):
        line = "2026-08-18 INF +  https://blue-fox-example.trycloudflare.com  +"
        m = setup._TUNNEL_URL_RE.search(line)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(0), "https://blue-fox-example.trycloudflare.com")
        self.assertIsNone(setup._TUNNEL_URL_RE.search("https://evil.example.com"))

    def test_resolve_public_base_prefers_configured(self):
        os.environ["PLIVO_SMS_PUBLIC_URL"] = "https://stable.example.com/"
        os.environ["PLIVO_WEBHOOK_URL"] = "https://legacy.example.com/webhooks/plivo"
        base, proc = asyncio.run(setup.resolve_public_base(8090))
        self.assertEqual(base, "https://stable.example.com")
        self.assertIsNone(proc)

    def test_resolve_public_base_falls_back_to_legacy_host(self):
        os.environ["PLIVO_WEBHOOK_URL"] = "https://legacy.example.com/webhooks/plivo"
        base, proc = asyncio.run(setup.resolve_public_base(8090))
        self.assertEqual(base, "https://legacy.example.com")
        self.assertIsNone(proc)

    def test_auto_wire_disabled_short_circuits(self):
        os.environ["PLIVO_SMS_AUTO_WIRE"] = "false"
        self.assertFalse(asyncio.run(setup.auto_wire("https://x.example.com")))

    def test_auto_wire_skips_without_credentials(self):
        self.assertFalse(asyncio.run(setup.auto_wire("https://x.example.com")))

    def test_get_scoped_secret_env_fallback(self):
        os.environ["PLIVO_TEST_SECRET_XYZ"] = "v1"
        self.assertEqual(setup.get_scoped_secret("PLIVO_TEST_SECRET_XYZ"), "v1")
        os.environ.pop("PLIVO_TEST_SECRET_XYZ")
        self.assertEqual(setup.get_scoped_secret("PLIVO_TEST_SECRET_XYZ", "d"), "d")


if __name__ == "__main__":
    unittest.main()
