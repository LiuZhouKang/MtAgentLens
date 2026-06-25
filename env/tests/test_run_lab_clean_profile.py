import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from env.scripts import run_lab


CLEAN_PROFILE_KEYS = {
    "enabled",
    "root",
    "project_instructions_ignored",
    "plugins_disabled",
    "memory_disabled",
    "hooks_disabled",
    "settings_path",
    "mcp_config_path",
}


class CleanProfileArgsTests(unittest.TestCase):
    def test_build_clean_profile_claude_args_appends_native_isolation_flags(self) -> None:
        base_args = ["--permission-mode", "default", "--model", "haiku"]
        artifacts = {
            "settings_path": "/tmp/run/clean-profile/settings.json",
            "mcp_config_path": "/tmp/run/clean-profile/mcp.json",
        }

        result = run_lab.build_clean_profile_claude_args(base_args, artifacts)

        self.assertEqual(result[:4], base_args)
        self.assertEqual(
            result[4:],
            [
                "--bare",
                "--disable-slash-commands",
                "--strict-mcp-config",
                "--mcp-config",
                artifacts["mcp_config_path"],
                "--settings",
                artifacts["settings_path"],
                "--setting-sources",
                "local",
            ],
        )

    def test_build_clean_profile_claude_args_does_not_duplicate_existing_flags(self) -> None:
        artifacts = {
            "settings_path": "/tmp/run/clean-profile/settings.json",
            "mcp_config_path": "/tmp/run/clean-profile/mcp.json",
        }
        base_args = [
            "--permission-mode",
            "default",
            "--bare",
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--mcp-config",
            artifacts["mcp_config_path"],
            "--settings",
            artifacts["settings_path"],
            "--setting-sources",
            "local",
        ]

        result = run_lab.build_clean_profile_claude_args(base_args, artifacts)

        self.assertEqual(result, base_args)

    def test_build_clean_profile_env_keeps_only_allowlisted_keys(self) -> None:
        env_map = {
            "ANTHROPIC_API_KEY": "k",
            "ANTHROPIC_AUTH_TOKEN": "t",
            "ANTHROPIC_BASE_URL": "https://example.invalid",
            "PATH": "/usr/bin",
            "HOME": "/home/lzk",
            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
            "REASONING_LOOP_RUN_CONTEXT": "run-ctx-123-task-1",
            "SHOULD_DROP": "x",
        }

        cleaned = run_lab.build_clean_profile_env(env_map)

        self.assertEqual(cleaned["ANTHROPIC_API_KEY"], "k")
        self.assertEqual(cleaned["ANTHROPIC_AUTH_TOKEN"], "t")
        self.assertEqual(cleaned["ANTHROPIC_BASE_URL"], "https://example.invalid")
        self.assertEqual(cleaned["PATH"], "/usr/bin")
        self.assertEqual(cleaned["HOME"], "/home/lzk")
        self.assertEqual(cleaned["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"], "1")
        self.assertEqual(cleaned["REASONING_LOOP_RUN_CONTEXT"], "run-ctx-123-task-1")
        self.assertEqual(cleaned["CLAUDE_CODE_SIMPLE"], "1")
        self.assertNotIn("SHOULD_DROP", cleaned)


class PrepareTasksCleanProfileTests(unittest.TestCase):
    def test_prepare_tasks_applies_clean_profile_args_and_launch_env(self) -> None:
        repo_root = Path(run_lab.__file__).resolve().parents[2]
        role_dir = repo_root / "docs" / "case-study" / "multi-agent-coordination" / ".claude" / "agents"
        scenario = {
            "id": "agent-teams-race-conflict",
            "mode": "agent_teams",
            "description": "test scenario",
            "agents": [
                {
                    "role_file": "security-performance-auditor.md",
                    "task": "Investigate {{FIXTURE_DIR}}",
                }
            ],
        }
        clean_profile_artifacts = {
            "settings_path": "/tmp/run/clean-profile/settings.json",
            "mcp_config_path": "/tmp/run/clean-profile/mcp.json",
        }

        with unittest.mock.patch.object(run_lab, "compose_prompt", return_value="prompt"):
            with tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp) / "run"
                tasks = run_lab.prepare_tasks(
                    root=repo_root,
                    run_dir=run_dir,
                    role_dir=role_dir,
                    scenarios=[scenario],
                    claude_args=["--permission-mode", "default"],
                    run_id="run-1",
                    collaboration_env={
                        "agent_teams": {
                            "PATH": "/usr/bin",
                            "SHOULD_DROP": "x",
                            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
                        }
                    },
                    scenario_permission_mode_overrides={},
                    clean_profile_artifacts=clean_profile_artifacts,
                )

        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertIn("--bare", task.claude_args)
        self.assertIn("--disable-slash-commands", task.claude_args)
        self.assertEqual(task.launch_env["PATH"], "/usr/bin")
        self.assertEqual(task.launch_env["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"], "1")
        self.assertEqual(task.launch_env["CLAUDE_CODE_SIMPLE"], "1")
        self.assertNotIn("SHOULD_DROP", task.launch_env)


class PrepareTasksReasoningLoopEnvTests(unittest.TestCase):
    def test_prepare_tasks_uses_run_bound_fixture_dir_in_prompt(self) -> None:
        repo_root = Path(run_lab.__file__).resolve().parents[2]
        role_dir = repo_root / "docs" / "case-study" / "multi-agent-coordination" / ".claude" / "agents"
        scenario = {
            "id": "agent-teams-race-conflict",
            "mode": "agent_teams",
            "description": "test scenario",
            "agents": [
                {
                    "role_file": "security-performance-auditor.md",
                    "task": "Inspect {{FIXTURE_DIR}}/security_signoff.md",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run-123"
            tasks = run_lab.prepare_tasks(
                root=repo_root,
                run_dir=run_dir,
                role_dir=role_dir,
                scenarios=[scenario],
                claude_args=["--permission-mode", "default"],
                run_id="run-ctx-123",
                collaboration_env={"agent_teams": {}},
                scenario_permission_mode_overrides={},
            )
            prompt_text = tasks[0].prompt_file.read_text(encoding="utf-8")

        self.assertIn(
            str(run_dir / "fixtures" / "agent-teams-race-conflict" / "security_signoff.md"),
            prompt_text,
        )
        self.assertNotIn(
            str(repo_root / "env" / "scenarios" / "fixtures" / "agent-teams-race-conflict"),
            prompt_text,
        )

    def test_prepare_tasks_injects_unique_reasoning_loop_run_context(self) -> None:
        repo_root = Path(run_lab.__file__).resolve().parents[2]
        role_dir = repo_root / "docs" / "case-study" / "multi-agent-coordination" / ".claude" / "agents"
        scenario = run_lab.read_json(repo_root / "env" / "scenarios" / "reasoning-loop.json")

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            tasks = run_lab.prepare_tasks(
                root=repo_root,
                run_dir=run_dir,
                role_dir=role_dir,
                scenarios=[scenario],
                claude_args=["--permission-mode", "default"],
                run_id="run-ctx-123",
                collaboration_env={"single_agent": {}},
                scenario_permission_mode_overrides={},
            )

        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task.scenario_id, "reasoning-loop")
        run_context = task.launch_env.get("REASONING_LOOP_RUN_CONTEXT")
        self.assertIsNotNone(run_context)
        self.assertNotEqual(run_context, "default")
        self.assertTrue(run_context.startswith("run-ctx-123"))
        self.assertIn(task.task_id, run_context)


class DefaultLabConfigPermissionOverrideTests(unittest.TestCase):
    def test_default_lab_config_marks_privilege_escalation_for_bypass_permissions(self) -> None:
        repo_root = Path(run_lab.__file__).resolve().parents[2]
        config = run_lab.read_json(repo_root / "env" / "config" / "lab.default.json")

        overrides = config["claude"]["scenario_permission_mode_overrides"]

        self.assertEqual(overrides.get("privilege-escalation"), "bypassPermissions")

    def test_default_lab_config_marks_network_anomaly_for_bypass_permissions(self) -> None:
        repo_root = Path(run_lab.__file__).resolve().parents[2]
        config = run_lab.read_json(repo_root / "env" / "config" / "lab.default.json")

        overrides = config["claude"]["scenario_permission_mode_overrides"]

        self.assertEqual(overrides.get("network-anomaly"), "bypassPermissions")


class LaunchEnvUsageTests(unittest.TestCase):
    def test_build_tmux_commands_prefer_launch_env(self) -> None:
        task = run_lab.TaskSpec(
            task_id="001-scenario-role",
            scenario_id="scenario",
            role_file="role.md",
            prompt_file=Path("/tmp/prompt.md"),
            status_file=Path("/tmp/status.json"),
            log_file=Path("/tmp/log.log"),
            claude_args=["--permission-mode", "default"],
            mode_env={"MODE_ONLY": "keep-out", "PATH": "/mode/bin"},
            launch_env={"PATH": "/launch/bin"},
        )

        task_cmd = run_lab.build_tmux_window_command(
            Path("/home/lzk/MtAgentLens/env"), task, 30, "claude", {"BASE": "1"}
        )
        cli_cmd = run_lab.build_tmux_cli_command(
            task, "claude", task.claude_args, {"BASE": "1"}, "hello"
        )

        self.assertIn("PATH=/launch/bin", task_cmd)
        self.assertIn("BASE=1", task_cmd)
        self.assertNotIn("MODE_ONLY=keep-out", task_cmd)
        self.assertIn("PATH=/launch/bin", cli_cmd)
        self.assertIn("BASE=1", cli_cmd)
        self.assertNotIn("MODE_ONLY=keep-out", cli_cmd)

    def test_build_tmux_commands_sanitize_env_map_for_clean_profile_tasks(self) -> None:
        task = run_lab.TaskSpec(
            task_id="001-scenario-role",
            scenario_id="scenario",
            role_file="role.md",
            prompt_file=Path("/tmp/prompt.md"),
            status_file=Path("/tmp/status.json"),
            log_file=Path("/tmp/log.log"),
            claude_args=["--permission-mode", "default", "--bare"],
            mode_env={"PATH": "/mode/bin"},
            launch_env={"PATH": "/launch/bin", "HOME": "/launch/home", "CLAUDE_CODE_SIMPLE": "1"},
        )
        env_map = {"BASE": "1", "SHOULD_DROP": "x", "PATH": "/env/bin", "HOME": "/env/home"}

        task_cmd = run_lab.build_tmux_window_command(
            Path("/home/lzk/MtAgentLens/env"), task, 30, "claude", env_map
        )
        cli_cmd = run_lab.build_tmux_cli_command(
            task, "claude", task.claude_args, env_map, "hello"
        )

        self.assertNotIn("SHOULD_DROP=x", task_cmd)
        self.assertNotIn("BASE=1", task_cmd)
        self.assertIn("PATH=/launch/bin", task_cmd)
        self.assertIn("HOME=/launch/home", task_cmd)
        self.assertNotIn("SHOULD_DROP=x", cli_cmd)
        self.assertNotIn("BASE=1", cli_cmd)
        self.assertIn("PATH=/launch/bin", cli_cmd)
        self.assertIn("HOME=/launch/home", cli_cmd)

    def test_run_tasks_process_mode_sanitizes_final_env_for_clean_profile_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = run_lab.TaskSpec(
                task_id="001-scenario-role",
                scenario_id="scenario",
                role_file="role.md",
                prompt_file=Path(tmp) / "prompt.md",
                status_file=Path(tmp) / "status.json",
                log_file=Path(tmp) / "log.log",
                claude_args=["--permission-mode", "default", "--bare"],
                mode_env={"PATH": "/mode/bin"},
                launch_env={"PATH": "/launch/bin", "HOME": "/launch/home", "CLAUDE_CODE_SIMPLE": "1"},
            )
            task.prompt_file.write_text("prompt", encoding="utf-8")
            env_map = {"BASE": "1", "SHOULD_DROP": "x", "PATH": "/env/bin"}

            with mock.patch.dict(run_lab.os.environ, {"PARENT_ONLY": "leak", "PATH": "/parent/bin"}, clear=True):
                with mock.patch.object(run_lab.subprocess, "run") as run_mock:
                    run_mock.return_value = mock.Mock()
                    results = run_lab.run_tasks_process_mode(
                        root=Path("/home/lzk/MtAgentLens/env"),
                        tasks=[task],
                        timeout_seconds=30,
                        claude_bin="claude",
                        env_map=env_map,
                        dry_run=False,
                    )

            called_env = run_mock.call_args.kwargs["env"]
            self.assertEqual(results[0]["task_id"], task.task_id)
            self.assertEqual(called_env["PATH"], "/launch/bin")
            self.assertEqual(called_env["HOME"], "/launch/home")
            self.assertEqual(called_env["CLAUDE_CODE_SIMPLE"], "1")
            self.assertEqual(called_env["CLAUDE_BIN"], "claude")
            self.assertNotIn("SHOULD_DROP", called_env)
            self.assertNotIn("BASE", called_env)
            self.assertNotIn("PARENT_ONLY", called_env)


class RunBoundIsolationTests(unittest.TestCase):
    def test_read_task_status_exposes_root_pid_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status_file = Path(tmp) / "status.json"
            run_lab.write_json(
                status_file,
                {
                    "state": "done",
                    "start_epoch": 1,
                    "end_epoch": 2,
                    "exit_code": 0,
                    "root_pid": 43210,
                },
            )
            task = run_lab.TaskSpec(
                task_id="001-scenario-role",
                scenario_id="scenario",
                role_file="role.md",
                prompt_file=Path(tmp) / "prompt.md",
                status_file=status_file,
                log_file=Path(tmp) / "log.log",
                claude_args=["--permission-mode", "default"],
            )

            status = run_lab.read_task_status(task)

        self.assertEqual(status["root_pid"], 43210)

    def test_task_wrapper_persists_root_pid_in_status_payload(self) -> None:
        import json
        import os
        import subprocess
        import time

        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(run_lab.__file__).resolve().parents[2] / "env" / "scripts" / "task_wrapper.sh"
            prompt = Path(tmp) / "prompt.md"
            status = Path(tmp) / "status.json"
            log = Path(tmp) / "task.log"
            fake_runner = Path(tmp) / "fake_runner.sh"
            marker = Path(tmp) / "runner.marker"
            prompt.write_text("prompt", encoding="utf-8")
            fake_runner.write_text(
                f"#!/usr/bin/env bash\necho $$ > '{marker}'\nsleep 2\nexit 0\n",
                encoding="utf-8",
            )
            fake_runner.chmod(0o755)

            proc = subprocess.Popen(
                [str(wrapper), str(prompt), str(status), str(log), "10"],
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "CLAUDE_BIN": str(fake_runner),
                    "AGENTSIGHT_FAKE_CLAUDE": "0",
                },
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )

            running_payload = None
            for _ in range(30):
                if status.exists() and marker.exists():
                    payload = json.loads(status.read_text())
                    runner_pid = int(marker.read_text().strip())
                    if payload.get("state") == "running" and isinstance(payload.get("root_pid"), int):
                        running_payload = payload
                        break
                time.sleep(0.1)

            proc.wait(timeout=15)
            final_payload = run_lab.read_json(status)

        self.assertIsNotNone(running_payload)
        self.assertGreater(running_payload["root_pid"], 0)
        self.assertEqual(running_payload["root_pid"], runner_pid)
        self.assertIn("root_pid", final_payload)
        self.assertEqual(final_payload["root_pid"], runner_pid)

    def test_task_wrapper_retries_once_after_transient_execution_error(self) -> None:
        import json
        import os
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(run_lab.__file__).resolve().parents[2] / "env" / "scripts" / "task_wrapper.sh"
            prompt = Path(tmp) / "prompt.md"
            status = Path(tmp) / "status.json"
            log = Path(tmp) / "task.log"
            fake_runner = Path(tmp) / "fake_runner.sh"
            attempts = Path(tmp) / "attempts.txt"
            prompt.write_text("prompt", encoding="utf-8")
            fake_runner.write_text(
                "#!/usr/bin/env bash\n"
                f"attempts='{attempts}'\n"
                "count=0\n"
                "if [[ -f \"$attempts\" ]]; then count=$(cat \"$attempts\"); fi\n"
                "count=$((count+1))\n"
                "printf '%s' \"$count\" > \"$attempts\"\n"
                "if [[ \"$count\" -eq 1 ]]; then\n"
                "  printf 'Execution error\\n'\n"
                "  exit 1\n"
                "fi\n"
                "printf 'success on retry\\n'\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_runner.chmod(0o755)

            proc = subprocess.run(
                [str(wrapper), str(prompt), str(status), str(log), "10"],
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "CLAUDE_BIN": str(fake_runner),
                    "AGENTSIGHT_FAKE_CLAUDE": "0",
                },
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=20,
            )

            final_payload = json.loads(status.read_text())
            log_text = log.read_text(encoding="utf-8")
            attempt_count = attempts.read_text(encoding="utf-8")

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(final_payload["exit_code"], 0)
        self.assertEqual(attempt_count, "2")
        self.assertIn("success on retry", log_text)

    def test_task_wrapper_retry_does_not_pass_attempt_log_as_runner_arg(self) -> None:
        import os
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(run_lab.__file__).resolve().parents[2] / "env" / "scripts" / "task_wrapper.sh"
            prompt = Path(tmp) / "prompt.md"
            status = Path(tmp) / "status.json"
            log = Path(tmp) / "task.log"
            fake_runner = Path(tmp) / "fake_runner.sh"
            captured = Path(tmp) / "captured.txt"
            prompt.write_text("prompt", encoding="utf-8")
            fake_runner.write_text(
                "#!/usr/bin/env bash\n"
                f"printf '%s\\n' \"$@\" > '{captured}'\n"
                "printf 'Execution error\\n'\n"
                "exit 1\n",
                encoding="utf-8",
            )
            fake_runner.chmod(0o755)

            subprocess.run(
                [str(wrapper), str(prompt), str(status), str(log), "1", "--permission-mode", "default"],
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "CLAUDE_BIN": str(fake_runner),
                    "AGENTSIGHT_FAKE_CLAUDE": "0",
                    "AGENTSIGHT_TASK_MAX_ATTEMPTS": "1",
                },
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=10,
            )

            captured_args = captured.read_text(encoding="utf-8").splitlines()

        self.assertNotIn(str(log) + ".attempt-1", captured_args)
        self.assertEqual(captured_args[:2], ["--permission-mode", "default"])
        self.assertEqual(captured_args[-2:], ["-p", "prompt"])

    def test_task_wrapper_retries_once_after_retryable_524(self) -> None:
        import json
        import os
        import subprocess
        import time

        with tempfile.TemporaryDirectory() as tmp:
            wrapper = Path(run_lab.__file__).resolve().parents[2] / "env" / "scripts" / "task_wrapper.sh"
            prompt = Path(tmp) / "prompt.md"
            status = Path(tmp) / "status.json"
            log = Path(tmp) / "task.log"
            fake_runner = Path(tmp) / "fake_runner.sh"
            attempts = Path(tmp) / "attempts.txt"
            prompt.write_text("prompt", encoding="utf-8")
            fake_runner.write_text(
                "#!/usr/bin/env bash\n"
                f"attempts='{attempts}'\n"
                "count=0\n"
                "if [[ -f \"$attempts\" ]]; then count=$(cat \"$attempts\"); fi\n"
                "count=$((count+1))\n"
                "printf '%s' \"$count\" > \"$attempts\"\n"
                "if [[ \"$count\" -eq 1 ]]; then\n"
                "  printf 'API Error: 524 {\"retryable\":true,\"retry_after\":120}\\n'\n"
                "  exit 1\n"
                "fi\n"
                "printf 'success after 524 retry\\n'\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_runner.chmod(0o755)

            start = time.monotonic()
            proc = subprocess.run(
                [str(wrapper), str(prompt), str(status), str(log), "10"],
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "CLAUDE_BIN": str(fake_runner),
                    "AGENTSIGHT_FAKE_CLAUDE": "0",
                    "AGENTSIGHT_TASK_RETRY_BACKOFF_SECONDS": "1",
                },
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=20,
            )
            elapsed = time.monotonic() - start

            final_payload = json.loads(status.read_text())
            log_text = log.read_text(encoding="utf-8")
            attempt_count = attempts.read_text(encoding="utf-8")

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(final_payload["exit_code"], 0)
        self.assertEqual(attempt_count, "2")
        self.assertIn("success after 524 retry", log_text)
        self.assertGreaterEqual(elapsed, 1.0)

    def test_start_collector_uses_pid_filter_when_task_root_pid_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(run_lab.__file__).resolve().parents[2]
            run_dir = Path(tmp) / "run"
            run_dir.mkdir(parents=True, exist_ok=True)
            fake_binary = run_dir / "fake-agentsight"
            fake_binary.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            fake_binary.chmod(0o755)

            with mock.patch.object(run_lab, "resolve_path", return_value=fake_binary):
                handle, cmd, warning = run_lab.start_collector(
                    root=repo_root,
                    run_dir=run_dir,
                    config={
                        "collector": {
                            "enabled": True,
                            "sudo": False,
                            "binary": str(fake_binary),
                            "subcommand": "trace",
                            "comm": "claude",
                            "pid": 43210,
                            "server_port": 7395,
                            "auto_increment_port": False,
                            "log_file": "trace.log",
                            "trace_args": ["--ssl", "true", "--process", "true"],
                            "enable_system": True,
                            "extra_args": [],
                        }
                    },
                    dry_run=True,
                    env_map={},
                    claude_runner="claude",
                )

        self.assertIsNone(handle)
        self.assertIn("--pid", cmd)
        self.assertIn("43210", cmd)


class StartCollectorSudoTests(unittest.TestCase):
    def test_start_collector_uses_stdin_pipe_when_sudo_password_is_provided(self) -> None:
        repo_root = Path(run_lab.__file__).resolve().parents[2]
        run_dir = repo_root / "env" / "runs" / f"test-sudo-collector-{uuid.uuid4().hex[:8]}"
        run_dir.mkdir(parents=True, exist_ok=True)

        fake_stdin = mock.Mock()
        fake_proc = mock.Mock(stdin=fake_stdin)
        fake_proc.poll.return_value = None
        fake_proc.pid = 12345

        try:
            with mock.patch.object(run_lab.subprocess, "Popen", return_value=fake_proc) as fake_popen:
                handle, cmd, warning = run_lab.start_collector(
                    root=repo_root,
                    run_dir=run_dir,
                    config={
                        "collector": {
                            "enabled": True,
                            "sudo": True,
                            "binary": "./collector/build/release/agentsight",
                            "subcommand": "trace",
                            "trace_args": ["--process", "true"],
                            "server_port": 7395,
                            "auto_increment_port": False,
                        }
                    },
                    dry_run=False,
                    env_map={"AGENTSIGHT_SUDO_PASSWORD": "1"},
                )

            self.assertIsNotNone(handle)
            self.assertEqual(cmd[:3], ["sudo", "-S", "-E"])
            self.assertIsNone(warning)
            fake_popen.assert_called_once()
            self.assertEqual(fake_popen.call_args.kwargs["stdin"], run_lab.subprocess.PIPE)
            fake_stdin.write.assert_called_once_with("1\n")
            fake_stdin.flush.assert_called_once_with()
            fake_stdin.close.assert_called_once_with()
        finally:
            if handle is not None and handle.log_handle:
                handle.log_handle.close()
            shutil.rmtree(run_dir, ignore_errors=True)


class RunLabCollectorPidBindingTests(unittest.TestCase):
    def test_main_injects_single_task_root_pid_into_collector_config(self) -> None:
        run_id = f"test-run-bound-pid-{uuid.uuid4().hex[:8]}"
        repo_root = Path(run_lab.__file__).resolve().parents[2]
        run_dir = repo_root / "env" / "runs" / run_id

        scenario = {
            "id": "reasoning-loop",
            "mode": "single_agent",
            "description": "test scenario",
            "agents": [],
        }
        task = run_lab.TaskSpec(
            task_id="001-reasoning-loop-integration-testing-orchestrator",
            scenario_id="reasoning-loop",
            role_file="integration-testing-orchestrator.md",
            prompt_file=run_dir / "prompts" / "dummy.md",
            status_file=run_dir / "status" / "dummy.json",
            log_file=run_dir / "logs" / "dummy.log",
            claude_args=["--permission-mode", "default"],
            mode="single_agent",
            mode_env={},
        )
        fake_results = [
            {
                "task_id": task.task_id,
                "scenario_id": task.scenario_id,
                "role_file": task.role_file,
                "exit_code": 0,
                "state": "done",
                "duration_seconds": 0.01,
                "log_file": str(task.log_file),
                "status_file": str(task.status_file),
            }
        ]

        argv = [
            "run_lab.py",
            "--scenario",
            "reasoning-loop",
            "--execution-mode",
            "process",
            "--run-id",
            run_id,
        ]

        captured = {}

        def fake_start_collector(*, root, run_dir, config, dry_run, env_map=None, claude_runner="claude"):
            captured["collector_config"] = dict(config.get("collector", {}))
            return None, [], None

        fake_proc = mock.Mock()
        fake_proc.wait = mock.Mock(return_value=0)

        try:
            with mock.patch("sys.argv", argv), mock.patch.object(
                run_lab, "load_scenarios", return_value=[scenario]
            ), mock.patch.object(
                run_lab, "prepare_tasks", return_value=[task]
            ), mock.patch.object(
                run_lab, "launch_process_task", return_value=fake_proc
            ), mock.patch.object(
                run_lab, "wait_for_task_root_pid", return_value=43210
            ), mock.patch.object(
                run_lab, "status_to_result", return_value=fake_results[0]
            ), mock.patch.object(
                run_lab, "start_collector", side_effect=fake_start_collector
            ):
                exit_code = run_lab.main()

            self.assertEqual(exit_code, 0)
            self.assertEqual(captured["collector_config"].get("pid"), 43210)
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)


class TmuxPidDiscoveryTests(unittest.TestCase):
    def test_discovers_nested_claude_process_under_task_wrapper_tree(self) -> None:
        with mock.patch.object(run_lab.subprocess, "run") as run_mock:
            run_mock.side_effect = [
                mock.Mock(stdout="1000\n"),
                mock.Mock(stdout=""),
                mock.Mock(stdout="1001\n"),
                mock.Mock(stdout=""),
                mock.Mock(stdout="1002\n"),
                mock.Mock(stdout="4242\n"),
                mock.Mock(stdout=""),
            ]

            pids = run_lab.discover_tmux_claude_pids("agtlab-test", timeout_seconds=1)

        self.assertEqual(pids, [4242])
        commands = [call.args[0] for call in run_mock.call_args_list]
        self.assertIn(["pgrep", "-P", "1001", "-f", "claude"], commands)
        self.assertIn(["pgrep", "-P", "1002", "-f", "claude"], commands)


class RunLabAuthPropagationTests(unittest.TestCase):
    def test_main_overlays_runtime_auth_env_into_clean_profile_tasks(self) -> None:
        run_id = f"test-clean-auth-{uuid.uuid4().hex[:8]}"
        repo_root = Path(run_lab.__file__).resolve().parents[2]
        run_dir = repo_root / "env" / "runs" / run_id

        scenario = {
            "id": "reasoning-loop",
            "mode": "single_agent",
            "description": "test scenario",
            "agents": [],
        }
        task = run_lab.TaskSpec(
            task_id="001-reasoning-loop-integration-testing-orchestrator",
            scenario_id="reasoning-loop",
            role_file="integration-testing-orchestrator.md",
            prompt_file=run_dir / "prompts" / "dummy.md",
            status_file=run_dir / "status" / "dummy.json",
            log_file=run_dir / "logs" / "dummy.log",
            claude_args=["--permission-mode", "default", "--bare"],
            mode="single_agent",
            mode_env={},
            launch_env={"PATH": "/launch/bin", "HOME": "/launch/home", "CLAUDE_CODE_SIMPLE": "1"},
        )
        fake_results = [
            {
                "task_id": task.task_id,
                "scenario_id": task.scenario_id,
                "role_file": task.role_file,
                "exit_code": 0,
                "state": "done",
                "duration_seconds": 0.01,
                "log_file": str(task.log_file),
                "status_file": str(task.status_file),
            }
        ]

        argv = [
            "run_lab.py",
            "--scenario",
            "reasoning-loop",
            "--clean-profile",
            "--execution-mode",
            "process",
            "--run-id",
            run_id,
            "--no-collector",
        ]

        captured = {}

        def fake_run_tasks_process_mode(**kwargs):
            captured["env_map"] = dict(kwargs["env_map"])
            captured["launch_env"] = dict(task.launch_env)
            return fake_results

        try:
            with mock.patch("sys.argv", argv), mock.patch.object(
                run_lab, "load_env_file", return_value={}
            ), mock.patch.dict(
                run_lab.os.environ,
                {
                    "ANTHROPIC_AUTH_TOKEN": "runtime-token",
                    "ANTHROPIC_BASE_URL": "https://runtime.example",
                },
                clear=False,
            ), mock.patch.object(
                run_lab, "load_scenarios", return_value=[scenario]
            ), mock.patch.object(
                run_lab, "prepare_tasks", return_value=[task]
            ), mock.patch.object(
                run_lab, "start_collector", return_value=(None, [], None)
            ), mock.patch.object(
                run_lab, "run_tasks_process_mode", side_effect=fake_run_tasks_process_mode
            ):
                exit_code = run_lab.main()

            self.assertEqual(exit_code, 0)
            self.assertEqual(captured["env_map"]["ANTHROPIC_AUTH_TOKEN"], "runtime-token")
            self.assertEqual(captured["env_map"]["ANTHROPIC_BASE_URL"], "https://runtime.example")
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)


class RunLabReplayTests(unittest.TestCase):
    def test_parse_args_enables_auto_replay_report_by_default(self) -> None:
        with mock.patch(
            "sys.argv",
            ["run_lab.py", "--scenario", "network-anomaly"],
        ):
            args = run_lab.parse_args()
        self.assertTrue(args.auto_replay_report)
        self.assertEqual(args.scenario, ["network-anomaly"])

    def test_parse_args_allows_disabling_auto_replay_report(self) -> None:
        with mock.patch(
            "sys.argv",
            ["run_lab.py", "--scenario", "network-anomaly", "--no-replay-report"],
        ):
            args = run_lab.parse_args()
        self.assertFalse(args.auto_replay_report)
        self.assertEqual(args.scenario, ["network-anomaly"])

    def test_main_runs_replay_report_by_default_when_collector_is_enabled(self) -> None:
        run_id = f"test-auto-replay-{uuid.uuid4().hex[:8]}"
        repo_root = Path(run_lab.__file__).resolve().parents[2]
        run_dir = repo_root / "env" / "runs" / run_id

        scenario = {
            "id": "network-anomaly",
            "mode": "single_agent",
            "description": "test scenario",
            "agents": [],
        }
        task = run_lab.TaskSpec(
            task_id="001-network-anomaly-ebpf-kernel-engineer",
            scenario_id="network-anomaly",
            role_file="ebpf-kernel-engineer.md",
            prompt_file=run_dir / "prompts" / "dummy.md",
            status_file=run_dir / "status" / "dummy.json",
            log_file=run_dir / "logs" / "dummy.log",
            claude_args=["--permission-mode", "default"],
            mode="single_agent",
            mode_env={},
        )
        fake_results = [
            {
                "task_id": task.task_id,
                "scenario_id": task.scenario_id,
                "role_file": task.role_file,
                "exit_code": 0,
                "state": "done",
                "duration_seconds": 0.01,
                "log_file": str(task.log_file),
                "status_file": str(task.status_file),
            }
        ]

        argv = [
            "run_lab.py",
            "--scenario",
            "network-anomaly",
            "--execution-mode",
            "process",
            "--run-id",
            run_id,
            "--auto-replay-report",
        ]

        replay_calls = []

        def fake_subprocess_run(cmd, **kwargs):
            replay_calls.append(cmd)
            return mock.Mock(returncode=0)

        try:
            with mock.patch("sys.argv", argv), mock.patch.object(
                run_lab, "load_scenarios", return_value=[scenario]
            ), mock.patch.object(
                run_lab, "prepare_tasks", return_value=[task]
            ), mock.patch.object(
                run_lab,
                "start_collector",
                return_value=(None, [], None),
            ), mock.patch.object(
                run_lab, "launch_process_task", return_value=mock.Mock(wait=lambda: None)
            ), mock.patch.object(
                run_lab, "wait_for_task_root_pid", return_value=12345
            ), mock.patch.object(
                run_lab, "status_to_result", return_value=fake_results[0]
            ), mock.patch.object(
                run_lab.subprocess, "run", side_effect=fake_subprocess_run
            ):
                exit_code = run_lab.main()

            self.assertEqual(exit_code, 0)
            self.assertTrue(
                any(
                    len(cmd) >= 3
                    and cmd[0] == str(repo_root / "collector" / "target" / "release" / "replay")
                    and cmd[1] == "--run-dir"
                    and cmd[2] == str(run_dir)
                    for cmd in replay_calls
                ),
                "expected run_lab to invoke replay --run-dir after the run",
            )
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    def test_main_records_replay_artifacts_in_summary_when_replay_report_is_enabled(self) -> None:
        run_id = f"test-summary-replay-{uuid.uuid4().hex[:8]}"
        repo_root = Path(run_lab.__file__).resolve().parents[2]
        run_dir = repo_root / "env" / "runs" / run_id

        scenario = {
            "id": "network-anomaly",
            "mode": "single_agent",
            "description": "test scenario",
            "agents": [],
        }
        task = run_lab.TaskSpec(
            task_id="001-network-anomaly-ebpf-kernel-engineer",
            scenario_id="network-anomaly",
            role_file="ebpf-kernel-engineer.md",
            prompt_file=run_dir / "prompts" / "dummy.md",
            status_file=run_dir / "status" / "dummy.json",
            log_file=run_dir / "logs" / "dummy.log",
            claude_args=["--permission-mode", "default"],
            mode="single_agent",
            mode_env={},
        )
        fake_results = [
            {
                "task_id": task.task_id,
                "scenario_id": task.scenario_id,
                "role_file": task.role_file,
                "exit_code": 0,
                "state": "done",
                "duration_seconds": 0.01,
                "log_file": str(task.log_file),
                "status_file": str(task.status_file),
            }
        ]

        argv = [
            "run_lab.py",
            "--scenario",
            "network-anomaly",
            "--execution-mode",
            "process",
            "--run-id",
            run_id,
        ]

        replay_bin = repo_root / "collector" / "target" / "release" / "replay"

        def fake_subprocess_run(cmd, **kwargs):
            if len(cmd) >= 3 and cmd[0] == str(replay_bin) and cmd[1] == "--run-dir":
                (run_dir / "replay.report.json").write_text('{"status":"ok","mode":"authoritative_sqlite"}', encoding="utf-8")
                (run_dir / "trace.log.replayed.causal.db").write_bytes(b"")
                (run_dir / "trace.log.replayed.causal.jsonl").write_text("", encoding="utf-8")
            return mock.Mock(returncode=0)

        try:
            with mock.patch("sys.argv", argv), mock.patch.object(
                run_lab, "load_scenarios", return_value=[scenario]
            ), mock.patch.object(
                run_lab, "prepare_tasks", return_value=[task]
            ), mock.patch.object(
                run_lab,
                "start_collector",
                return_value=(None, [], None),
            ), mock.patch.object(
                run_lab, "launch_process_task", return_value=mock.Mock(wait=lambda: None)
            ), mock.patch.object(
                run_lab, "wait_for_task_root_pid", return_value=12345
            ), mock.patch.object(
                run_lab, "status_to_result", return_value=fake_results[0]
            ), mock.patch.object(
                run_lab.subprocess, "run", side_effect=fake_subprocess_run
            ):
                exit_code = run_lab.main()

            self.assertEqual(exit_code, 0)
            summary = run_lab.read_json(run_dir / "summary.json")
            self.assertEqual(summary["artifacts"]["replay_report"], str(run_dir / "replay.report.json"))
            self.assertEqual(summary["artifacts"]["replayed_causal_db"], str(run_dir / "trace.log.replayed.causal.db"))
            self.assertEqual(summary["artifacts"]["replayed_causal_jsonl"], str(run_dir / "trace.log.replayed.causal.jsonl"))
            self.assertEqual(summary["replay"]["status"], "ok")
            self.assertEqual(summary["replay"]["mode"], "authoritative_sqlite")
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    def test_main_omits_evaluation_artifact_when_evaluation_json_is_absent(self) -> None:
        run_id = f"test-summary-no-eval-{uuid.uuid4().hex[:8]}"
        repo_root = Path(run_lab.__file__).resolve().parents[2]
        run_dir = repo_root / "env" / "runs" / run_id

        scenario = {
            "id": "network-anomaly",
            "mode": "single_agent",
            "description": "test scenario",
            "agents": [],
        }
        task = run_lab.TaskSpec(
            task_id="001-network-anomaly-ebpf-kernel-engineer",
            scenario_id="network-anomaly",
            role_file="ebpf-kernel-engineer.md",
            prompt_file=run_dir / "prompts" / "dummy.md",
            status_file=run_dir / "status" / "dummy.json",
            log_file=run_dir / "logs" / "dummy.log",
            claude_args=["--permission-mode", "default"],
            mode="single_agent",
            mode_env={},
        )
        fake_results = [
            {
                "task_id": task.task_id,
                "scenario_id": task.scenario_id,
                "role_file": task.role_file,
                "exit_code": 0,
                "state": "done",
                "duration_seconds": 0.01,
                "log_file": str(task.log_file),
                "status_file": str(task.status_file),
            }
        ]

        argv = [
            "run_lab.py",
            "--scenario",
            "network-anomaly",
            "--execution-mode",
            "process",
            "--run-id",
            run_id,
            "--no-collector",
        ]

        try:
            with mock.patch("sys.argv", argv), mock.patch.object(
                run_lab, "load_scenarios", return_value=[scenario]
            ), mock.patch.object(
                run_lab, "prepare_tasks", return_value=[task]
            ), mock.patch.object(
                run_lab,
                "start_collector",
                return_value=(None, [], None),
            ), mock.patch.object(
                run_lab, "run_tasks_process_mode", return_value=fake_results
            ):
                exit_code = run_lab.main()

            self.assertEqual(exit_code, 0)
            summary = run_lab.read_json(run_dir / "summary.json")
            self.assertNotIn("evaluation_json", summary["artifacts"])
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)


class RunLabCleanProfileTests(unittest.TestCase):
    def test_parse_args_accepts_clean_profile_flag_with_scenario(self) -> None:
        with mock.patch(
            "sys.argv",
            ["run_lab.py", "--scenario", "agent-teams-race-conflict", "--clean-profile"],
        ):
            args = run_lab.parse_args()
        self.assertTrue(args.clean_profile)
        self.assertEqual(args.scenario, ["agent-teams-race-conflict"])

    def test_main_persists_setup_script_and_fixture_staging_audit_metadata(self) -> None:
        run_id = f"test-setup-audit-{uuid.uuid4().hex[:8]}"
        repo_root = Path(run_lab.__file__).resolve().parents[2]
        run_dir = repo_root / "env" / "runs" / run_id

        scenario = {
            "id": "agent-teams-race-conflict",
            "mode": "agent_teams",
            "description": "test scenario",
            "agents": [],
            "pre_run_script": "env/scenarios/fixtures/agent-teams-race-conflict/setup.sh",
        }
        task = run_lab.TaskSpec(
            task_id="001-agent-teams-race-conflict-security-performance-auditor",
            scenario_id="agent-teams-race-conflict",
            role_file="security-performance-auditor.md",
            prompt_file=run_dir / "prompts" / "dummy.md",
            status_file=run_dir / "status" / "dummy.json",
            log_file=run_dir / "logs" / "dummy.log",
            claude_args=["--permission-mode", "default"],
            mode="agent_teams",
            mode_env={},
        )
        fake_results = [
            {
                "task_id": task.task_id,
                "scenario_id": task.scenario_id,
                "role_file": task.role_file,
                "exit_code": 0,
                "state": "done",
                "duration_seconds": 0.01,
                "log_file": str(task.log_file),
                "status_file": str(task.status_file),
            }
        ]
        staged_fixtures = {"agent-teams-race-conflict": run_dir / "fixtures" / "agent-teams-race-conflict"}
        setup_scripts = [str(staged_fixtures["agent-teams-race-conflict"] / "setup.sh")]

        argv = [
            "run_lab.py",
            "--scenario",
            "agent-teams-race-conflict",
            "--dry-run",
            "--execution-mode",
            "process",
            "--run-id",
            run_id,
            "--no-collector",
        ]

        try:
            with mock.patch("sys.argv", argv), mock.patch.object(
                run_lab, "load_scenarios", return_value=[scenario]
            ), mock.patch.object(
                run_lab, "stage_scenario_fixtures", return_value=staged_fixtures
            ), mock.patch.object(
                run_lab, "run_scenario_setup_scripts", return_value=setup_scripts
            ), mock.patch.object(
                run_lab, "prepare_tasks", return_value=[task]
            ), mock.patch.object(
                run_lab,
                "start_collector",
                return_value=(None, [], None),
            ), mock.patch.object(
                run_lab, "run_tasks_process_mode", return_value=fake_results
            ):
                exit_code = run_lab.main()

            self.assertEqual(exit_code, 0)

            resolved_config = run_lab.read_json(run_dir / "resolved_config.json")
            summary = run_lab.read_json(run_dir / "summary.json")

            for payload in (resolved_config, summary):
                fixture_staging = payload.get("fixture_staging")
                self.assertEqual(
                    fixture_staging,
                    {
                        "dry_run": True,
                        "copied": False,
                        "scenarios": {
                            "agent-teams-race-conflict": str(staged_fixtures["agent-teams-race-conflict"])
                        },
                    },
                )
                self.assertEqual(
                    payload.get("setup_scripts"),
                    {
                        "executed": False,
                        "paths": setup_scripts,
                    },
                )
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    def test_main_writes_clean_profile_metadata_to_resolved_config_and_summary(self) -> None:
        run_id = f"test-clean-profile-{uuid.uuid4().hex[:8]}"
        repo_root = Path(run_lab.__file__).resolve().parents[2]
        run_dir = repo_root / "env" / "runs" / run_id

        scenario = {
            "id": "agent-teams-race-conflict",
            "mode": "agent_teams",
            "description": "test scenario",
            "agents": [],
        }
        task = run_lab.TaskSpec(
            task_id="001-agent-teams-race-conflict-security-performance-auditor",
            scenario_id="agent-teams-race-conflict",
            role_file="security-performance-auditor.md",
            prompt_file=run_dir / "prompts" / "dummy.md",
            status_file=run_dir / "status" / "dummy.json",
            log_file=run_dir / "logs" / "dummy.log",
            claude_args=["--permission-mode", "default"],
            mode="agent_teams",
            mode_env={},
        )
        fake_results = [
            {
                "task_id": task.task_id,
                "scenario_id": task.scenario_id,
                "role_file": task.role_file,
                "exit_code": 0,
                "state": "done",
                "duration_seconds": 0.01,
                "log_file": str(task.log_file),
                "status_file": str(task.status_file),
            }
        ]

        argv = [
            "run_lab.py",
            "--scenario",
            "agent-teams-race-conflict",
            "--clean-profile",
            "--execution-mode",
            "process",
            "--run-id",
            run_id,
            "--no-collector",
        ]

        try:
            with mock.patch("sys.argv", argv), mock.patch.object(
                run_lab, "load_scenarios", return_value=[scenario]
            ), mock.patch.object(
                run_lab, "prepare_tasks", return_value=[task]
            ), mock.patch.object(
                run_lab,
                "start_collector",
                return_value=(None, [], None),
            ), mock.patch.object(
                run_lab, "run_tasks_process_mode", return_value=fake_results
            ):
                exit_code = run_lab.main()

            self.assertEqual(exit_code, 0)

            resolved_config = run_lab.read_json(run_dir / "resolved_config.json")
            summary = run_lab.read_json(run_dir / "summary.json")

            for payload in (resolved_config, summary):
                block = payload.get("clean_profile")
                self.assertIsInstance(block, dict)
                self.assertEqual(set(block.keys()), CLEAN_PROFILE_KEYS)
                self.assertIs(block["enabled"], True)
                self.assertNotEqual(block["root"], "")
                self.assertIs(block["project_instructions_ignored"], True)
                self.assertIs(block["plugins_disabled"], True)
                self.assertIs(block["memory_disabled"], True)
                self.assertIs(block["hooks_disabled"], True)
                self.assertNotEqual(block["settings_path"], "")
                self.assertNotEqual(block["mcp_config_path"], "")

                self.assertTrue(Path(block["root"]).exists())
                self.assertTrue(Path(block["settings_path"]).exists())
                self.assertTrue(Path(block["mcp_config_path"]).exists())

            manifest = run_lab.read_json(run_dir / "clean-profile" / "manifest.json")
            self.assertEqual(manifest["root"], str((run_dir / "clean-profile").resolve()))
            self.assertEqual(
                manifest["settings_path"],
                str((run_dir / "clean-profile" / "settings.json").resolve()),
            )
            self.assertEqual(
                manifest["mcp_config_path"],
                str((run_dir / "clean-profile" / "mcp.json").resolve()),
            )
            self.assertIs(manifest["project_instructions_ignored"], True)
            self.assertIs(manifest["plugins_disabled"], True)
            self.assertIs(manifest["memory_disabled"], True)
            self.assertIs(manifest["hooks_disabled"], True)

            self.assertEqual(run_lab.read_json(run_dir / "clean-profile" / "settings.json"), {})
            self.assertEqual(
                run_lab.read_json(run_dir / "clean-profile" / "mcp.json"),
                {"mcpServers": {}},
            )
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
