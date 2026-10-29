"""Unit tests for the auth-service benchmark workload."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import workload  # noqa: E402


class TokenTests(unittest.TestCase):
    def test_sign_and_verify_round_trip(self):
        token = workload.sign(workload.CLAIMS)
        self.assertTrue(workload.verify(token))

    def test_wrong_key_is_rejected(self):
        token = workload.sign(workload.CLAIMS)
        self.assertFalse(workload.verify(token, key=b"another-benchmark-key"))

    def test_tampered_signature_is_rejected(self):
        token = workload.sign(workload.CLAIMS)
        tampered = token[:-5] + ("A" if token[-5] != "A" else "B") + token[-4:]
        self.assertFalse(workload.verify(tampered))

    def test_malformed_token_is_rejected(self):
        self.assertFalse(workload.verify("not-a-token"))

    def test_claims_round_trip(self):
        self.assertEqual(workload.decode(workload.sign(workload.CLAIMS)), workload.CLAIMS)

    def test_workload_self_check(self):
        self.assertTrue(workload.check())


if __name__ == "__main__":
    unittest.main()
