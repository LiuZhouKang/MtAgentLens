import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

RUN_LAB_PATH = Path(__file__).resolve().parent / "run_lab.py"
spec = importlib.util.spec_from_file_location("run_lab", RUN_LAB_PATH)
run_lab = importlib.util.module_from_spec(spec)
sys.modules["run_lab"] = run_lab
spec.loader.exec_module(run_lab)


class RunLabScenarioSetupTests(unittest.TestCase):
    def test_stage_scenario_fixtures_copies_each_fixture_directory_into_run_dir(self):
        with unittest.mock.patch.object(run_lab, "resolve_path", side_effect=lambda root, raw: (root / raw).resolve()):
            with self.subTest("copies fixture contents into run-local directory"):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    run_dir = root / "env" / "runs" / "run-123"
                    source_dir = root / "env" / "scenarios" / "fixtures" / "a"
                    nested = source_dir / "nested"
                    nested.mkdir(parents=True, exist_ok=True)
                    (source_dir / "security_signoff.md").write_text("ok", encoding="utf-8")
                    (nested / "setup.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
                    scenarios = [{"id": "a"}, {"id": "missing"}]

                    staged = run_lab.stage_scenario_fixtures(root, run_dir, scenarios)

                    staged_dir = run_dir / "fixtures" / "a"
                    self.assertEqual(staged["a"], staged_dir)
                    self.assertTrue((staged_dir / "security_signoff.md").exists())
                    self.assertTrue((staged_dir / "nested" / "setup.sh").exists())
                    self.assertFalse((run_dir / "fixtures" / "missing").exists())

    def test_stage_scenario_fixtures_removes_stale_target_directory_before_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "env" / "runs" / "run-123"
            source_dir = root / "env" / "scenarios" / "fixtures" / "a"
            source_dir.mkdir(parents=True, exist_ok=True)
            (source_dir / "fresh.txt").write_text("fresh", encoding="utf-8")
            stale_dir = run_dir / "fixtures" / "a"
            stale_dir.mkdir(parents=True, exist_ok=True)
            (stale_dir / "stale.txt").write_text("stale", encoding="utf-8")

            staged = run_lab.stage_scenario_fixtures(root, run_dir, [{"id": "a"}])

            staged_dir = staged["a"]
            self.assertTrue((staged_dir / "fresh.txt").exists())
            self.assertFalse((staged_dir / "stale.txt").exists())

    def test_stage_scenario_fixtures_skips_copy_in_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "env" / "runs" / "run-123"
            source_dir = root / "env" / "scenarios" / "fixtures" / "a"
            source_dir.mkdir(parents=True, exist_ok=True)
            (source_dir / "security_signoff.md").write_text("ok", encoding="utf-8")

            staged = run_lab.stage_scenario_fixtures(root, run_dir, [{"id": "a"}], dry_run=True)

            self.assertEqual(staged["a"], run_dir / "fixtures" / "a")
            self.assertFalse((run_dir / "fixtures" / "a").exists())

    def test_run_scenario_setup_scripts_executes_staged_fixture_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "env" / "runs" / "run-123"
            staged_script = run_dir / "fixtures" / "a" / "setup.sh"
            staged_script.parent.mkdir(parents=True, exist_ok=True)
            staged_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            abs_script = root / "abs" / "setup-c.sh"
            abs_script.parent.mkdir(parents=True, exist_ok=True)
            abs_script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            scenarios = [
                {"id": "a", "pre_run_script": "env/scenarios/fixtures/a/setup.sh"},
                {"id": "b"},
                {"id": "c", "pre_run_script": str(abs_script)},
            ]

            calls = []

            def fake_run(cmd, cwd=None, check=None):
                calls.append((cmd, cwd, check))

            with mock.patch.object(run_lab.subprocess, "run", side_effect=fake_run):
                executed = run_lab.run_scenario_setup_scripts(root, run_dir, scenarios, dry_run=False)

        self.assertEqual(executed, [
            str(staged_script),
            str(abs_script),
        ])
        self.assertEqual(calls[0][0], [str(staged_script)])
        self.assertEqual(calls[0][1], str(root))
        self.assertTrue(calls[0][2])
        self.assertEqual(calls[1][0], [str(abs_script)])

    def test_run_scenario_setup_scripts_skips_execution_in_dry_run(self):
        root = Path("/repo")
        run_dir = Path("/repo/env/runs/run-123")
        scenarios = [{"id": "a", "pre_run_script": "env/scenarios/fixtures/a/setup.sh"}]

        with mock.patch.object(run_lab.subprocess, "run") as fake_run:
            executed = run_lab.run_scenario_setup_scripts(root, run_dir, scenarios, dry_run=True)

        self.assertEqual(executed, ["/repo/env/runs/run-123/fixtures/a/setup.sh"])
        fake_run.assert_not_called()

    def test_run_scenario_setup_scripts_raises_clear_error_when_staged_fixture_script_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "env" / "runs" / "run-123"
            static_fixture_dir = root / "env" / "scenarios" / "fixtures" / "a"
            static_fixture_dir.mkdir(parents=True, exist_ok=True)
            (static_fixture_dir / "setup.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

            with self.assertRaisesRegex(FileNotFoundError, "Staged fixture setup script not found"):
                run_lab.run_scenario_setup_scripts(
                    root,
                    run_dir,
                    [{"id": "a", "pre_run_script": "env/scenarios/fixtures/a/setup.sh"}],
                    dry_run=False,
                )


if __name__ == "__main__":
    unittest.main()
