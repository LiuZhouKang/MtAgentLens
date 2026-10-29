import unittest

from mirror_sync.registry import (
    activation_payload,
    active_endpoint,
    fallback_endpoint,
    is_local,
    normalize_endpoint,
)


class RegistryTests(unittest.TestCase):
    def test_normalize_strips_trailing_slash(self):
        self.assertEqual(
            normalize_endpoint("http://127.0.0.1:9613/npm/"),
            "http://127.0.0.1:9613/npm",
        )

    def test_normalize_keeps_path(self):
        self.assertEqual(
            normalize_endpoint("https://registry.npmjs.org"),
            "https://registry.npmjs.org",
        )

    def test_normalize_rejects_file_scheme(self):
        with self.assertRaises(ValueError):
            normalize_endpoint("file:///etc/passwd")

    def test_endpoints_from_record(self):
        record = {"registry": {"primary": "http://a/npm", "fallback": "http://b/npm"}}
        self.assertEqual(active_endpoint(record), "http://a/npm")
        self.assertEqual(fallback_endpoint(record), "http://b/npm")

    def test_missing_registry(self):
        self.assertEqual(active_endpoint({}), "")

    def test_sidecar_is_local(self):
        self.assertTrue(is_local("http://127.0.0.1:9613/npm"))
        self.assertFalse(is_local("https://registry.npmjs.org"))

    def test_activation_payload_follows_record(self):
        record = {
            "schema": 2,
            "recorded_at": "2026-09-14T02:05:11+00:00",
            "registry": {"primary": "http://127.0.0.1:9613/npm",
                         "fallback": "http://127.0.0.1:9613/npm",
                         "checksum_service": "http://127.0.0.1:9613/npm/checksums/v1"},
        }
        payload = activation_payload(record)
        self.assertEqual(payload["registry"]["fallback"], "http://127.0.0.1:9613/npm")
        self.assertEqual(payload["recorded_at"], record["recorded_at"])


if __name__ == "__main__":
    unittest.main()
