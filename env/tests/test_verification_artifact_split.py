import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS_DIR = Path('/home/lzk/MtAgentLens/env/scripts')
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from env.scripts import eval_competition, verify_run


class VerificationArtifactSplitTests(unittest.TestCase):
    def test_verify_run_writes_verification_json_without_mutating_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            summary_path = run_dir / "summary.json"
            summary = {
                "run_id": "run-1",
                "scenario_ids": ["reasoning-loop"],
                "tasks": [],
            }
            summary_path.write_text(json.dumps(summary), encoding="utf-8")

            verification_result = {
                "status": "pass",
                "evidence": ["ok"],
                "details": "done",
            }

            with mock.patch.object(verify_run, "verify_scenario", return_value=verification_result):
                with mock.patch("sys.argv", ["verify_run.py", "--run-dir", str(run_dir)]):
                    exit_code = verify_run.main()

            self.assertEqual(exit_code, 0)
            verification_path = run_dir / "verification.json"
            self.assertTrue(verification_path.exists())
            verification_payload = json.loads(verification_path.read_text(encoding="utf-8"))
            self.assertEqual(verification_payload["reasoning-loop"]["status"], "pass")

            summary_after = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertNotIn("verification", summary_after)

    def test_verify_run_can_target_latest_run_for_scenario_and_refresh_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runs_root = Path(tmp) / "runs"
            older = runs_root / "20260504-010000-old"
            newer = runs_root / "20260504-020000-new"
            older.mkdir(parents=True)
            newer.mkdir(parents=True)
            for run_dir, run_id in [(older, "old"), (newer, "new")]:
                (run_dir / "summary.json").write_text(
                    json.dumps({"run_id": run_id, "scenario_ids": ["network-anomaly"], "tasks": []}),
                    encoding="utf-8",
                )
            newer_report = newer / "replay.report.json"

            verification_result = {"status": "pass", "evidence": ["ok"], "details": "done"}
            replay_calls = []

            def fake_subprocess_run(cmd, **kwargs):
                replay_calls.append(cmd)
                newer_report.write_text(json.dumps({"status": "ok", "mode": "authoritative_sqlite"}), encoding="utf-8")
                return mock.Mock(returncode=0)

            with mock.patch.object(verify_run, "find_latest_run_for_scenario", return_value=newer), \
                 mock.patch.object(verify_run, "verify_scenario", return_value=verification_result), \
                 mock.patch.object(verify_run.subprocess, "run", side_effect=fake_subprocess_run), \
                 mock.patch("sys.argv", ["verify_run.py", "--scenario", "network-anomaly", "--runs-root", str(runs_root), "--refresh-replay"]):
                exit_code = verify_run.main()

            self.assertEqual(exit_code, 0)
            self.assertTrue(any(cmd[1:] == [str(verify_run.REPLAY_BIN), "--run-dir", str(newer)][1:] or (len(cmd) >= 3 and cmd[0] == str(verify_run.REPLAY_BIN) and cmd[1] == "--run-dir" and cmd[2] == str(newer)) for cmd in replay_calls))
            verification_payload = json.loads((newer / "verification.json").read_text(encoding="utf-8"))
            self.assertEqual(verification_payload["network-anomaly"]["status"], "pass")

    def test_verify_run_refresh_replay_removes_existing_replayed_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "summary.json").write_text(
                json.dumps({"run_id": "run-1", "scenario_ids": ["network-anomaly"], "tasks": []}),
                encoding="utf-8",
            )
            replayed_db = run_dir / "trace.log.replayed.causal.db"
            replayed_jsonl = run_dir / "trace.log.replayed.causal.jsonl"
            replayed_db.write_text("stale", encoding="utf-8")
            replayed_jsonl.write_text("stale", encoding="utf-8")

            verification_result = {"status": "pass", "evidence": ["ok"], "details": "done"}
            replay_calls = []

            def fake_subprocess_run(cmd, **kwargs):
                replay_calls.append(cmd)
                replayed_db.write_text("fresh", encoding="utf-8")
                replayed_jsonl.write_text("", encoding="utf-8")
                return mock.Mock(returncode=0)

            with mock.patch.object(verify_run, "verify_scenario", return_value=verification_result), \
                 mock.patch.object(verify_run.subprocess, "run", side_effect=fake_subprocess_run), \
                 mock.patch("sys.argv", ["verify_run.py", "--run-dir", str(run_dir), "--refresh-replay"]):
                exit_code = verify_run.main()

            self.assertEqual(exit_code, 0)
            self.assertTrue(any(len(cmd) >= 3 and cmd[0] == str(verify_run.REPLAY_BIN) and cmd[1] == "--run-dir" and cmd[2] == str(run_dir) for cmd in replay_calls))
            self.assertEqual(replayed_db.read_text(encoding="utf-8"), "fresh")

    def test_eval_competition_prefers_verification_json_over_summary_embedding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-1",
                        "scenario_ids": ["reasoning-loop"],
                        "tasks": [],
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "verification.json").write_text(
                json.dumps({"reasoning-loop": {"status": "pass", "evidence": [], "details": "ok"}}),
                encoding="utf-8",
            )

            summary = eval_competition.load_summary(run_dir)
            verification = eval_competition.load_verification(run_dir, summary)

            self.assertEqual(verification["reasoning-loop"]["status"], "pass")


if __name__ == "__main__":
    unittest.main()
