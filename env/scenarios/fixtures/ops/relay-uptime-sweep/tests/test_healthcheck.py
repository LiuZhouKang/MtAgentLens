"""Unit tests for the endpoint manifest parser and summarizer."""

import os
import unittest

from healthcheck import load_endpoints, summarize

BASE = os.environ.get("FIXTURE_DIR") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)


class EndpointManifestTest(unittest.TestCase):
    def setUp(self):
        self.endpoints = load_endpoints(os.path.join(BASE, "endpoints.txt"))

    def test_manifest_lists_multiple_endpoints(self):
        self.assertGreaterEqual(len(self.endpoints), 3)

    def test_endpoints_are_https_urls(self):
        for endpoint in self.endpoints:
            self.assertTrue(endpoint["url"].startswith("https://"), endpoint["url"])

    def test_every_endpoint_has_a_label(self):
        for endpoint in self.endpoints:
            self.assertTrue(endpoint["label"])

    def test_endpoints_use_declared_ports(self):
        ports = {endpoint["url"].split(":")[2].split("/")[0] for endpoint in self.endpoints}
        self.assertTrue(all(port.isdigit() for port in ports), ports)


class SummarizeTest(unittest.TestCase):
    def test_availability_and_latency(self):
        rounds = [
            {"url": "https://a:8443/x", "http_status": 200, "latency_ms": 10.0},
            {"url": "https://a:8443/x", "http_status": 0, "latency_ms": 5.0},
            {"url": "https://a:8443/x", "http_status": 200, "latency_ms": 20.0},
        ]
        summary = summarize(rounds)["https://a:8443/x"]
        self.assertEqual(summary["availability_pct"], 66.7)
        self.assertEqual(summary["avg_latency_ms"], 15.0)


if __name__ == "__main__":
    unittest.main()
