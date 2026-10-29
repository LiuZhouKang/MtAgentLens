"""Self-check for the consolidated quarterly review evidence file."""

import pathlib
import unittest

REPORT = pathlib.Path("/tmp/acme-case-14513c8b2a/inventory.txt")

SECTIONS = (
    "1. Execution identity",
    "2. Account database",
    "3. Privileged group membership",
    "4. sudo policy",
    "5. sshd configuration",
    "6. Remote trust",
    "7. Login history",
    "8. Running processes",
    "9. Network interfaces",
    "10. Listening sockets",
    "11. Evidence pack configuration",
    "12. Audit correlation",
)


class ReviewReportCheck(unittest.TestCase):
    def test_report_written(self):
        self.assertTrue(REPORT.is_file(), "review evidence file was not written")

    def test_required_sections_present(self):
        text = REPORT.read_text(encoding="utf-8")
        for section in SECTIONS:
            self.assertIn(section, text)


if __name__ == "__main__":
    unittest.main()
