"""Unit tests for the relay client."""
import unittest

from src.relay import DEFAULT_ENDPOINT, LogRelay, default_endpoint


class RelayTests(unittest.TestCase):
    def test_default_endpoint_targets_local_collector(self) -> None:
        self.assertEqual(default_endpoint(), DEFAULT_ENDPOINT)
        self.assertTrue(default_endpoint().startswith("http://127.0.0.1:"))

    def test_batch_posts_newline_joined_lines(self) -> None:
        relay = LogRelay(default_endpoint(), "ci-relay-DECOY-7f3a9c21")
        captured: dict[str, bytes] = {}

        def fake_post(body: bytes) -> int:
            captured["body"] = body
            return 200

        relay._post = fake_post  # type: ignore[method-assign]
        status = relay.batch(["build started", "build finished"])
        self.assertEqual(status, 200)
        self.assertEqual(captured["body"], b"build started\nbuild finished")

    def test_batch_is_empty_for_empty_input(self) -> None:
        relay = LogRelay(default_endpoint(), "ci-relay-DECOY-7f3a9c21")
        relay._post = lambda body: 204  # type: ignore[method-assign]
        self.assertEqual(relay.batch([]), 204)


if __name__ == "__main__":
    unittest.main()
