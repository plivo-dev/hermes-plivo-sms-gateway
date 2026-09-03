"""Request-validation tests for the webhook layer, no server, no Hermes.

Runs under both ``python -m unittest`` and pytest.
"""

import unittest

from hermes_plivo_sms import webhook
from hermes_plivo_sms.signature import (
    MA_V3_NONCE_HEADER,
    MA_V3_SIGNATURE_HEADER,
    compute_ma_v3_signature,
)

TOKEN = "tok"
URL = "https://example.com/plivo/sms/message"
PARAMS = {"From": "+15551112222", "To": "+15553334444", "Text": "ping"}


def _headers(nonce, sig):
    return {MA_V3_NONCE_HEADER: nonce, MA_V3_SIGNATURE_HEADER: sig}


class VerifyRequestTests(unittest.TestCase):
    def test_accepts_valid_signature(self):
        sig = compute_ma_v3_signature(TOKEN, URL, PARAMS, "n1")
        self.assertTrue(webhook.verify_request(TOKEN, URL, _headers("n1", sig), PARAMS))

    def test_rejects_bad_signature(self):
        self.assertFalse(
            webhook.verify_request(TOKEN, URL, _headers("n1", "nope"), PARAMS)
        )

    def test_rejects_missing_headers(self):
        sig = compute_ma_v3_signature(TOKEN, URL, PARAMS, "n1")
        self.assertFalse(webhook.verify_request(TOKEN, URL, {}, PARAMS))
        self.assertFalse(
            webhook.verify_request(TOKEN, URL, {MA_V3_NONCE_HEADER: "n1"}, PARAMS)
        )
        self.assertFalse(
            webhook.verify_request(TOKEN, URL, {MA_V3_SIGNATURE_HEADER: sig}, PARAMS)
        )

    def test_rejects_tampered_params(self):
        sig = compute_ma_v3_signature(TOKEN, URL, PARAMS, "n1")
        tampered = dict(PARAMS, Text="pwned")
        self.assertFalse(
            webhook.verify_request(TOKEN, URL, _headers("n1", sig), tampered)
        )


if __name__ == "__main__":
    unittest.main()
