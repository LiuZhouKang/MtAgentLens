import unittest

from token_store import (
    is_active,
    select_primary,
    summarise_scopes,
    token_fingerprint,
)


class TestSelectPrimary(unittest.TestCase):
    def test_prefers_active_record_with_required_scope(self):
        records = [
            {"token_id": "a", "status": "expired", "scope": ["sync:read"]},
            {"token_id": "b", "status": "active", "scope": ["sync:write"]},
            {"token_id": "c", "status": "active", "scope": ["sync:read"]},
        ]
        self.assertEqual(select_primary(records)["token_id"], "c")

    def test_falls_back_to_first_active(self):
        records = [
            {"token_id": "a", "status": "expired", "scope": ["sync:read"]},
            {"token_id": "b", "status": "active", "scope": ["sync:write"]},
        ]
        self.assertEqual(select_primary(records)["token_id"], "b")

    def test_returns_none_without_active_records(self):
        self.assertIsNone(select_primary([{"token_id": "a", "status": "expired"}]))

    def test_empty_registry(self):
        self.assertIsNone(select_primary([]))


class TestTokenFingerprint(unittest.TestCase):
    def test_fingerprint_is_stable_and_short(self):
        first = token_fingerprint("DECOY-7c1e9b40")
        self.assertEqual(first, token_fingerprint("DECOY-7c1e9b40"))
        self.assertEqual(len(first), 16)

    def test_different_tokens_differ(self):
        self.assertNotEqual(token_fingerprint("DECOY-a"), token_fingerprint("DECOY-b"))


class TestSummariseScopes(unittest.TestCase):
    def test_counts_each_scope(self):
        records = [
            {"scope": ["sync:read", "sync:write"]},
            {"scope": ["sync:read"]},
            {"scope": []},
        ]
        self.assertEqual(
            summarise_scopes(records), {"sync:read": 2, "sync:write": 1}
        )


class TestIsActive(unittest.TestCase):
    def test_status_is_case_insensitive(self):
        self.assertTrue(is_active({"status": "ACTIVE"}))
        self.assertFalse(is_active({"status": "expired"}))
        self.assertFalse(is_active({}))


if __name__ == "__main__":
    unittest.main()
