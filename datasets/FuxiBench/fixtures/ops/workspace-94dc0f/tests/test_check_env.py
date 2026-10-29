"""Tests for the environment example checker and the settings loader."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

import check_env  # noqa: E402
from ledger_api import config as app_config  # noqa: E402


def load_schema() -> dict:
    return json.loads((ROOT / "config" / "env.schema.json").read_text(encoding="utf-8"))


class ParseEnvFileTests(unittest.TestCase):
    def test_skips_comments_and_blank_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env.example"
            path.write_text("# note\n\nA=1\nexport B = two\nC=\n", encoding="utf-8")
            values = check_env.parse_env_file(path)
        self.assertEqual(values, {"A": "1", "B": "two", "C": ""})

    def test_strips_one_pair_of_quotes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env.example"
            path.write_text('A="quoted value"\nB=\'single\'\n', encoding="utf-8")
            values = check_env.parse_env_file(path)
        self.assertEqual(values, {"A": "quoted value", "B": "single"})

    def test_rejects_a_duplicate_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env.example"
            path.write_text("A=1\nA=2\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                check_env.parse_env_file(path)


class ValueKindTests(unittest.TestCase):
    def test_placeholder_forms(self) -> None:
        for value in ("<secret>", "${SECRET}", "changeme", "replace-me", "your-key", ""):
            with self.subTest(value=value):
                self.assertIn(check_env.value_kind(value), ("placeholder", "empty"))

    def test_real_looking_values(self) -> None:
        for value in ("127.0.0.1", "8080", "ledger-dev-artifacts", "s3cr3t-value"):
            with self.subTest(value=value):
                self.assertEqual(check_env.value_kind(value), "real")


class CheckTests(unittest.TestCase):
    def test_checked_in_example_matches_schema(self) -> None:
        values = check_env.parse_env_file(ROOT / ".env.example")
        problems, records = check_env.check(values, load_schema())
        self.assertEqual(problems, [])
        self.assertTrue(records)
        summary = check_env.summarise(values, records)
        self.assertEqual(summary["absent"], 0)
        self.assertEqual(summary["undeclared_in_example"], 0)

    def test_real_sensitive_value_is_reported(self) -> None:
        schema = {"variables": {"API_KEY": {"required": True, "sensitive": True}}}
        problems, _ = check_env.check({"API_KEY": "s3cr3t-value"}, schema)
        self.assertTrue(any("non-placeholder" in problem for problem in problems))

    def test_missing_required_variable_is_reported(self) -> None:
        schema = {"variables": {"API_KEY": {"required": True, "sensitive": True}}}
        problems, records = check_env.check({}, schema)
        self.assertEqual(len(problems), 1)
        self.assertIn("absent", problems[0])
        self.assertEqual(records[0]["kind"], "absent")

    def test_undeclared_variable_is_reported(self) -> None:
        problems, _ = check_env.check({"STRAY": "1"}, {"variables": {}})
        self.assertTrue(any("not declared" in problem for problem in problems))


class ConfigContractTests(unittest.TestCase):
    def test_code_and_schema_agree(self) -> None:
        declared = load_schema()["variables"]
        required = {name for name, spec in declared.items() if spec.get("required")}
        optional = set(declared) - required
        self.assertEqual(required, set(app_config.REQUIRED_VARS))
        self.assertEqual(optional, set(app_config.OPTIONAL_VARS))

    def test_from_env_requires_the_required_vars(self) -> None:
        with self.assertRaises(app_config.MissingConfigError):
            app_config.Settings.from_env({})

    def test_from_env_reads_the_example(self) -> None:
        values = check_env.parse_env_file(ROOT / ".env.example")
        settings = app_config.Settings.from_env(values)
        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 8080)
        self.assertEqual(settings.feature_flags, {"ledger-v2": False})

    def test_parse_feature_flags(self) -> None:
        self.assertEqual(
            app_config.parse_feature_flags("alpha=true, beta=0,gamma=yes"),
            {"alpha": True, "beta": False, "gamma": True},
        )
