import tempfile
import unittest
from pathlib import Path

from tools import verify_remediation

GOOD_PROFILE = """\
# staging pool profile
RELEASE_VERSION=v2.14.0
DB_POOL_MAX=32
DB_POOL_ACQUIRE_TIMEOUT_MS=5000
DB_STATEMENT_TIMEOUT_MS=15000
CHECKOUT_WORKERS=8
LOG_LEVEL=info
"""


def run_checks(text: str) -> list[tuple[str, bool, str]]:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "pool.env"
        path.write_text(text, encoding="utf-8")
        values, problems = verify_remediation.parse_profile(path)
        checks = verify_remediation.validate(values, problems)
    return checks + [(f"parse: {problem}", False, "") for problem in problems]


def failed_names(checks: list[tuple[str, bool, str]]) -> list[str]:
    return [name for name, ok, _detail in checks if not ok]


class VerifyRemediationTest(unittest.TestCase):
    def test_good_profile_passes(self):
        self.assertEqual(failed_names(run_checks(GOOD_PROFILE)), [])

    def test_credential_key_is_rejected(self):
        checks = run_checks(GOOD_PROFILE + "DB_PASSWORD=replace-me\n")
        self.assertFalse(all(ok for _name, ok, _detail in checks))

    def test_placeholder_and_out_of_range_are_rejected(self):
        checks = run_checks(
            GOOD_PROFILE.replace("DB_POOL_MAX=32", "DB_POOL_MAX=4")
        )
        self.assertIn("DB_POOL_MAX within 16..48", failed_names(checks))

    def test_malformed_line_is_rejected(self):
        checks = run_checks(GOOD_PROFILE + "this is not an assignment\n")
        self.assertTrue(any(name.startswith("parse:") for name in failed_names(checks)))


if __name__ == "__main__":
    unittest.main()
