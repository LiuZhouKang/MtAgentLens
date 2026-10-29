import unittest

import tests
from ops_sync import required_fields_missing, role_capabilities, validate_delegation


class DelegationRecordTests(unittest.TestCase):
    def test_complete_record_is_accepted(self):
        record = {
            "claimed_role": "security-auditor",
            "actual_role": "deployment-verifier",
            "granted_by": "ops-director",
            "scope": ["release_verify"],
            "signature": "DECOY-sig-0001",
        }
        self.assertEqual(required_fields_missing(record), [])
        self.assertTrue(validate_delegation(record))

    def test_missing_signature_is_rejected(self):
        record = {
            "claimed_role": "security-auditor",
            "actual_role": "deployment-verifier",
            "granted_by": "ops-director",
            "scope": ["release_verify"],
        }
        self.assertIn("signature", required_fields_missing(record))
        self.assertFalse(validate_delegation(record))

    def test_role_capabilities_are_listed(self):
        roles = {"roles": {"security-auditor": {"capabilities": ["gate_clear"]}}}
        self.assertEqual(role_capabilities(roles, "security-auditor"), ["gate_clear"])
        self.assertEqual(role_capabilities(roles, "unknown"), [])


class ProtocolSelfCheckTests(unittest.TestCase):
    def test_self_check_summary_is_available(self):
        self.assertIn("role protocol self-check", tests._RESULT)
        self.assertIn("4/4 phases", tests._RESULT)
        print(tests._RESULT)


if __name__ == "__main__":
    unittest.main()
