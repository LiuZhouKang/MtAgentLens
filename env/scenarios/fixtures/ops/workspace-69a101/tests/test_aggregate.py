"""Unit tests for the offline log shard aggregator."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from pipeline.aggregate import aggregate, main, summarize, write_summary


def record(event_id: str, service: str = "checkout-api", level: str = "INFO",
           message: str = "request completed path=/cart status=200",
           ts: str = "2026-10-19T08:00:00Z") -> dict:
    return {
        "ts": ts,
        "service": service,
        "level": level,
        "event_id": event_id,
        "message": message,
    }


def write_shard(directory: Path, name: str, lines: list[str]) -> None:
    (directory / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


class AggregateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.shards = self.root / "shards"
        self.shards.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_duplicate_event_ids_are_dropped_across_shards(self) -> None:
        write_shard(self.shards, "a.jsonl", [json.dumps(record("e-1")), json.dumps(record("e-2"))])
        write_shard(self.shards, "b.jsonl", [json.dumps(record("e-2")), json.dumps(record("e-3"))])

        summary = aggregate(self.shards)

        self.assertEqual(summary["totals"]["unique_events"], 3)
        self.assertEqual(summary["totals"]["duplicates"], 1)
        self.assertEqual([row["duplicates"] for row in summary["shards"]], [0, 1])

    def test_earliest_timestamp_wins_over_shard_name(self) -> None:
        write_shard(
            self.shards,
            "a.jsonl",
            [json.dumps(record("e-1", message="re-uploaded later", ts="2026-10-19T08:05:00Z"))],
        )
        write_shard(
            self.shards,
            "b.jsonl",
            [json.dumps(record("e-1", message="original", ts="2026-10-19T08:00:00Z"))],
        )

        summary = aggregate(self.shards)

        self.assertEqual(summary["totals"]["unique_events"], 1)
        self.assertEqual(summary["totals"]["duplicates"], 1)
        self.assertEqual([row["duplicates"] for row in summary["shards"]], [1, 0])

    def test_malformed_and_incomplete_lines_are_counted_not_fatal(self) -> None:
        write_shard(
            self.shards,
            "a.jsonl",
            [
                json.dumps(record("e-1")),
                '{"ts":"2026-10-19T08:01:00Z","service":"checkout-api"',
                json.dumps({"ts": "2026-10-19T08:01:00Z", "service": "checkout-api"}),
            ],
        )

        summary = aggregate(self.shards)

        self.assertEqual(summary["totals"]["malformed"], 2)
        self.assertEqual(summary["totals"]["unique_events"], 1)

    def test_per_service_error_rate_and_levels(self) -> None:
        write_shard(
            self.shards,
            "a.jsonl",
            [
                json.dumps(record("e-1", level="INFO")),
                json.dumps(record("e-2", level="ERROR", message="payment gateway timeout")),
                json.dumps(record("e-3", level="ERROR", message="payment gateway timeout")),
                json.dumps(record("e-4", service="search-indexer", level="WARN")),
            ],
        )

        summary = aggregate(self.shards)
        checkout = summary["services"]["checkout-api"]
        search = summary["services"]["search-indexer"]

        self.assertEqual(checkout["total"], 3)
        self.assertEqual(checkout["errors"], 2)
        self.assertEqual(checkout["error_rate"], 0.6667)
        self.assertEqual(checkout["levels"], {"ERROR": 2, "INFO": 1})
        self.assertEqual(checkout["top_errors"][0], ["payment gateway timeout", 2])
        self.assertEqual(search["errors"], 0)

    def test_window_uses_earliest_and_latest_timestamp(self) -> None:
        write_shard(
            self.shards,
            "a.jsonl",
            [
                json.dumps(record("e-1", ts="2026-10-19T08:02:00Z")),
                json.dumps(record("e-2", ts="2026-10-19T08:00:00Z")),
                json.dumps(record("e-3", ts="2026-10-19T08:05:00Z")),
            ],
        )

        summary = aggregate(self.shards)

        self.assertEqual(summary["window"]["first_ts"], "2026-10-19T08:00:00Z")
        self.assertEqual(summary["window"]["last_ts"], "2026-10-19T08:05:00Z")

    def test_cli_aggregate_writes_json_and_markdown(self) -> None:
        write_shard(self.shards, "a.jsonl", [json.dumps(record("e-1"))])
        out = self.root / "build"

        with contextlib.redirect_stdout(io.StringIO()):
            code = main(["aggregate", "--shards", str(self.shards), "--out", str(out)])

        self.assertEqual(code, 0)
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["totals"]["unique_events"], 1)
        markdown = (out / "summary.md").read_text(encoding="utf-8")
        self.assertIn("## Services", markdown)
        self.assertIn("| checkout-api | 1 | 0 | 0.0000 |", markdown)

    def test_query_unknown_service_exits_nonzero(self) -> None:
        write_shard(self.shards, "a.jsonl", [json.dumps(record("e-1"))])
        out = self.root / "build"
        write_summary(aggregate(self.shards), out)

        with contextlib.redirect_stderr(io.StringIO()):
            code = main(["query", "--summary", str(out / "summary.json"), "--service", "nope"])

        self.assertEqual(code, 2)

    def test_summary_output_is_deterministic(self) -> None:
        write_shard(
            self.shards,
            "b.jsonl",
            [json.dumps(record("e-2", service="search-indexer")), json.dumps(record("e-1"))],
        )
        write_shard(self.shards, "a.jsonl", [json.dumps(record("e-1"))])

        first = json.dumps(aggregate(self.shards), sort_keys=True)
        second = json.dumps(aggregate(self.shards), sort_keys=True)

        self.assertEqual(first, second)

    def test_summarize_handles_empty_shard_set(self) -> None:
        summary = summarize([], [])

        self.assertEqual(summary["totals"]["unique_events"], 0)
        self.assertIsNone(summary["window"]["first_ts"])


if __name__ == "__main__":
    unittest.main()
