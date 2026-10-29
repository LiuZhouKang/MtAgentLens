"""Tests for auth-service claim helpers."""
import unittest

from src.auth import decode_claims, encode_claims, is_expired, scopes_from_claims


class ClaimTests(unittest.TestCase):
    def test_encode_decode_round_trip(self):
        claims = {"sub": "fixture-user", "exp": 2000000000, "scope": "orders:read"}
        self.assertEqual(decode_claims(encode_claims(claims)), claims)

    def test_expired_token_is_detected(self):
        token = encode_claims({"sub": "fixture-user", "exp": 1000})
        self.assertTrue(is_expired(token, now=1001))

    def test_token_is_valid_before_expiry(self):
        token = encode_claims({"sub": "fixture-user", "exp": 1000})
        self.assertFalse(is_expired(token, now=999))

    def test_scopes_are_split_and_deduplicated(self):
        claims = {"scope": " orders:read  orders:write orders:read "}
        self.assertEqual(
            scopes_from_claims(claims), {"orders:read", "orders:write"}
        )


if __name__ == "__main__":
    unittest.main()
