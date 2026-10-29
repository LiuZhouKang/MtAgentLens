import unittest
from pathlib import Path
from urllib.parse import urlparse

BASE = Path(__file__).resolve().parent.parent
LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def endpoint_lines() -> list[str]:
    raw = (BASE / "endpoints.txt").read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in raw if line.strip() and not line.lstrip().startswith("#")]


class EndpointListTest(unittest.TestCase):
    def test_eleven_unique_entries(self):
        lines = endpoint_lines()
        self.assertEqual(len(lines), 11)
        urls = [line.split()[0] for line in lines]
        self.assertEqual(len(set(urls)), 11)

    def test_loopback_and_http_only(self):
        for line in endpoint_lines():
            parsed = urlparse(line.split()[0])
            self.assertIn(parsed.scheme, ("http", "https"))
            self.assertIn(parsed.hostname, LOOPBACK)
            self.assertIsNotNone(parsed.port)

    def test_runbook_documents_driver(self):
        text = (BASE / "docs.md").read_text(encoding="utf-8")
        self.assertIn("make sync", text)
        self.assertIn("baseline-report.json", text)


if __name__ == "__main__":
    unittest.main()
