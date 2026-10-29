"""Tests for config parsing and static safety checks."""

import unittest

from releasekit.config import (
    host_is_reserved,
    is_placeholder,
    looks_live_secret,
    parse_env,
    parse_known_hosts,
)


class ParseEnvTests(unittest.TestCase):
    def test_parses_pairs_and_skips_comments(self):
        values = parse_env("# release config\nPROFILE=release\n\nRETRIES=3\n")
        self.assertEqual(values, {"PROFILE": "release", "RETRIES": "3"})

    def test_strips_matching_quotes(self):
        self.assertEqual(parse_env("TOKEN='abc'")["TOKEN"], "abc")

    def test_strips_double_quotes(self):
        self.assertEqual(parse_env('HOST="deploy-01.example.invalid"')["HOST"], "deploy-01.example.invalid")

    def test_rejects_line_without_equals(self):
        with self.assertRaises(ValueError):
            parse_env("BROKEN LINE")

    def test_rejects_empty_key(self):
        with self.assertRaises(ValueError):
            parse_env("=value")


class PlaceholderTests(unittest.TestCase):
    def test_decoy_marker_is_placeholder(self):
        self.assertTrue(is_placeholder("DECOY-RELEASE-TOKEN-NOT-REAL"))

    def test_angle_bracket_template_is_placeholder(self):
        self.assertTrue(is_placeholder("<your-token-here>"))

    def test_live_aws_key_is_not_placeholder(self):
        self.assertFalse(is_placeholder("AKIAABCDEFGHIJKLMNOP"))


class KnownHostsTests(unittest.TestCase):
    def test_parses_plain_and_bracketed_hosts(self):
        entries = parse_known_hosts(
            "deploy-01.example.invalid ssh-ed25519 AAAADECOYKEY1\n"
            "[deploy-jump.example.invalid]:2222 ssh-rsa AAAADECOYKEY2\n"
        )
        self.assertEqual([entry.host for entry in entries], [
            "deploy-01.example.invalid",
            "deploy-jump.example.invalid",
        ])

    def test_rejects_wrong_field_count(self):
        with self.assertRaises(ValueError):
            parse_known_hosts("deploy-01.example.invalid ssh-ed25519")

    def test_rejects_unknown_key_type(self):
        with self.assertRaises(ValueError):
            parse_known_hosts("deploy-01.example.invalid ssh-dss AAAADECOYKEY")

    def test_reserved_example_domain(self):
        self.assertTrue(host_is_reserved("deploy-01.example.invalid"))

    def test_production_domain_is_not_reserved(self):
        self.assertFalse(host_is_reserved("deploy-01.corp.internal"))


class SecretShapeTests(unittest.TestCase):
    def test_private_key_header_is_detected(self):
        self.assertTrue(looks_live_secret("-----BEGIN OPENSSH PRIVATE KEY-----"))

    def test_decoy_token_is_not_live(self):
        self.assertFalse(looks_live_secret("DECOY-RELEASE-TOKEN-NOT-REAL"))

    def test_api_token_shape_is_live(self):
        self.assertTrue(looks_live_secret("sk-" + "a" * 24))


if __name__ == "__main__":
    unittest.main()
