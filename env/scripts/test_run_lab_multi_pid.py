import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

RUN_LAB_PATH = Path(__file__).resolve().parent / "run_lab.py"
spec = importlib.util.spec_from_file_location("run_lab", RUN_LAB_PATH)
run_lab = importlib.util.module_from_spec(spec)
sys.modules["run_lab"] = run_lab
spec.loader.exec_module(run_lab)


def make_task(task_id: str):
    base = Path(f"/tmp/{task_id}")
    return run_lab.TaskSpec(
        task_id=task_id,
        scenario_id="collaborative-dev",
        role_file="integration-testing-orchestrator.md",
        prompt_file=base.with_suffix(".prompt.md"),
        status_file=base.with_suffix(".status.json"),
        log_file=base.with_suffix(".log"),
        claude_args=["--permission-mode", "default"],
    )


class RunLabMultiPidTests(unittest.TestCase):
    def test_main_applies_pid_set_before_starting_collector_in_tmux_mode(self):
        repo_root = Path(run_lab.__file__).resolve().parents[2]
        run_id = "test-tmux-multipid"
        run_dir = repo_root / "env" / "runs" / run_id
        if run_dir.exists():
            import shutil
            shutil.rmtree(run_dir)

        fake_results = [
            {
                "task_id": "001-agent-teams-role-hijack-frontend-visualization-expert",
                "scenario_id": "agent-teams-role-hijack",
                "role_file": "frontend-visualization-expert.md",
                "exit_code": 0,
                "state": "done",
                "duration_seconds": 1.0,
                "log_file": str(run_dir / "logs" / "001.log"),
                "status_file": str(run_dir / "status" / "001.json"),
            },
            {
                "task_id": "002-agent-teams-role-hijack-rust-framework-architect",
                "scenario_id": "agent-teams-role-hijack",
                "role_file": "rust-framework-architect.md",
                "exit_code": 0,
                "state": "done",
                "duration_seconds": 1.0,
                "log_file": str(run_dir / "logs" / "002.log"),
                "status_file": str(run_dir / "status" / "002.json"),
            },
        ]

        captured = {}

        def fake_start_collector(**kwargs):
            captured["collector_cfg"] = kwargs["config"]["collector"]
            return None, ["collector"], None

        def fake_run_tasks_tmux_mode(**kwargs):
            startup_hook = kwargs.get("startup_hook")
            if startup_hook is not None:
                startup_hook()
            return fake_results, None

        with mock.patch.object(
            sys,
            "argv",
            [
                "run_lab.py",
                "--config",
                str(repo_root / "env" / "config" / "lab.default.json"),
                "--scenario",
                "agent-teams-role-hijack",
                "--execution-mode",
                "tmux",
                "--tmux-pane-mode",
                "task",
                "--run-id",
                run_id,
                "--clean-profile",
            ],
        ), mock.patch.object(run_lab, "run_scenario_setup_scripts", return_value=[]), mock.patch.object(
            run_lab,
            "run_tasks_tmux_mode",
            side_effect=fake_run_tasks_tmux_mode,
        ), mock.patch.object(
            run_lab,
            "collect_task_root_pids",
            return_value=[1111, 2222],
        ), mock.patch.object(
            run_lab,
            "start_collector",
            side_effect=fake_start_collector,
        ), mock.patch.object(
            run_lab,
            "stop_collector",
            return_value=None,
        ):
            exit_code = run_lab.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured["collector_cfg"].get("pid_set"), [1111, 2222])
        self.assertNotIn("pid", captured["collector_cfg"])

    def test_main_propagates_runtime_sudo_password_into_tmux_collector_startup(self):
        repo_root = Path(run_lab.__file__).resolve().parents[2]
        run_id = "test-tmux-sudo-propagation"
        run_dir = repo_root / "env" / "runs" / run_id
        if run_dir.exists():
            import shutil
            shutil.rmtree(run_dir)

        fake_results = [
            {
                "task_id": "001-network-anomaly-ebpf-kernel-engineer",
                "scenario_id": "network-anomaly",
                "role_file": "ebpf-kernel-engineer.md",
                "exit_code": 0,
                "state": "done",
                "duration_seconds": 1.0,
                "log_file": str(run_dir / "logs" / "001.log"),
                "status_file": str(run_dir / "status" / "001.json"),
            }
        ]

        captured = {}

        def fake_start_collector(**kwargs):
            captured["env_map"] = dict(kwargs.get("env_map") or {})
            return None, ["collector"], None

        def fake_run_tasks_tmux_mode(**kwargs):
            startup_hook = kwargs.get("startup_hook")
            if startup_hook is not None:
                startup_hook()
            return fake_results, None

        with mock.patch.object(
            sys,
            "argv",
            [
                "run_lab.py",
                "--config",
                str(repo_root / "env" / "config" / "lab.default.json"),
                "--scenario",
                "network-anomaly",
                "--execution-mode",
                "tmux",
                "--tmux-pane-mode",
                "task",
                "--run-id",
                run_id,
                "--clean-profile",
            ],
        ), mock.patch.dict(run_lab.os.environ, {"MTAGENTLENS_SUDO_PASSWORD": "1"}, clear=False), mock.patch.object(
            run_lab, "run_scenario_setup_scripts", return_value=[]
        ), mock.patch.object(
            run_lab,
            "run_tasks_tmux_mode",
            side_effect=fake_run_tasks_tmux_mode,
        ), mock.patch.object(
            run_lab,
            "collect_task_root_pids",
            return_value=[1111],
        ), mock.patch.object(
            run_lab,
            "start_collector",
            side_effect=fake_start_collector,
        ), mock.patch.object(
            run_lab,
            "stop_collector",
            return_value=None,
        ):
            exit_code = run_lab.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(captured["env_map"].get("MTAGENTLENS_SUDO_PASSWORD"), "1")

    def test_collect_task_root_pids_returns_all_running_root_pids(self):
        tasks = [make_task("task-a"), make_task("task-b"), make_task("task-c")]
        fake_roots = {
            "task-a": 1111,
            "task-b": None,
            "task-c": 3333,
        }

        def fake_wait(task, timeout_seconds=10):
            return fake_roots[task.task_id]

        with mock.patch.object(run_lab, "wait_for_task_root_pid", side_effect=fake_wait):
            root_pids = run_lab.collect_task_root_pids(tasks, timeout_seconds=1)

        self.assertEqual(root_pids, [1111, 3333])

    def test_apply_collector_pid_targets_prefers_pid_set_when_multiple_roots(self):
        collector_cfg = {"enabled": True, "comm": "claude"}

        updated = run_lab.apply_collector_pid_targets(collector_cfg, [1111, 2222])

        self.assertEqual(updated["pid_set"], [1111, 2222])
        self.assertNotIn("pid", updated)

    def test_apply_collector_pid_targets_uses_single_pid_when_only_one_root(self):
        collector_cfg = {"enabled": True, "comm": "claude"}

        updated = run_lab.apply_collector_pid_targets(collector_cfg, [7777])

        self.assertEqual(updated["pid"], 7777)
        self.assertNotIn("pid_set", updated)


if __name__ == "__main__":
    unittest.main()
