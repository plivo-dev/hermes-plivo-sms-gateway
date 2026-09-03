"""MA-V3 signature vectors, pure stdlib, no Hermes runtime required.

Runs under both ``python -m unittest`` and pytest.
"""

import unittest

from hermes_plivo_sms import signature


class SignatureTests(unittest.TestCase):
    def test_signature_roundtrip(self):
        token = "test_token"
        url = "https://example.com/plivo/sms/message"
        params = {
            "From": "+15551112222",
            "To": "+15553334444",
            "Text": "hi",
            "MessageUUID": "abc",
        }
        nonce = "nonce123"
        sig = signature.compute_ma_v3_signature(token, url, params, nonce)
        self.assertTrue(signature.verify_ma_v3_signature(token, url, params, nonce, sig))
        self.assertFalse(
            signature.verify_ma_v3_signature(token, url, params, nonce, "wrong")
        )
        self.assertFalse(signature.verify_ma_v3_signature(token, url, params, "", sig))
        self.assertFalse(signature.verify_ma_v3_signature("", url, params, nonce, sig))

    def test_payload_is_sorted_by_key_and_nonce_appended(self):
        payload = signature.build_ma_v3_payload("https://x/y", {"b": "2", "a": "1"}, "N")
        self.assertEqual(payload, "https://x/y?a1b2.N")

    def test_payload_includes_sorted_query_segment(self):
        payload = signature.build_ma_v3_payload(
            "https://x/y?zz=9&aa=1", {"b": "2", "a": "1"}, "N"
        )
        self.assertEqual(payload, "https://x/y?aa=1&zz=9.a1b2.N")

    def test_fragment_is_stripped_from_signed_url(self):
        with_fragment = signature.build_ma_v3_payload(
            "https://x/y#frag", {"a": "1"}, "N"
        )
        without = signature.build_ma_v3_payload("https://x/y", {"a": "1"}, "N")
        self.assertEqual(with_fragment, without)

    def test_comma_joined_signature_candidates_accepted(self):
        token = "tok"
        url = "https://example.com/plivo/sms/message"
        params = {"From": "+15551112222", "Text": "hello"}
        nonce = "n1"
        good = signature.compute_ma_v3_signature(token, url, params, nonce)
        self.assertTrue(
            signature.verify_ma_v3_signature(token, url, params, nonce, f"bad,{good}")
        )
        self.assertFalse(
            signature.verify_ma_v3_signature(token, url, params, nonce, "bad,also-bad")
        )

    def test_verified_algorithm_shape_matches_documented_formula(self):
        # base + "?" + key-sorted key+value concatenation + "." + nonce
        payload = signature.build_ma_v3_payload(
            "https://h.example/plivo/sms/message",
            {"To": "14150000000", "From": "14151111111", "Text": "Yo"},
            "abcdef",
        )
        self.assertEqual(
            payload,
            "https://h.example/plivo/sms/message?"
            "From14151111111TextYoTo14150000000.abcdef",
        )


if __name__ == "__main__":
    unittest.main()
