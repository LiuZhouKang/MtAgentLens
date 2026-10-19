# SPDX-License-Identifier: Apache-2.0 OR MIT
# Copyright (c) 2026 MtAgentLens Project
"""Contract tests for the benchmark clean profile and capture isolation.

`env/scripts/run_lab.py` (and its `--clean-profile`) was removed when the
benchmark moved to `mtagentlens_benchmark`. These tests pin the replacement:
the agent's configuration is isolated per run, and the agent is exec'd under a
unique `comm` so the collector cannot capture the interactive session.
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mtagentlens_benchmark.engine import clean_profile


class CleanProfileArtifactsTests(unittest.TestCase):
    def test_artifacts_disable_plugins_hooks_memory_and_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = clean_profile.create_artifacts(Path(tmp))

            settings = Path(artifacts["settings_path"]).read_text()
            self.assertIn('"enabledPlugins": {}', settings)
            self.assertIn('"hooks": {}', settings)
            self.assertIn('"mcpServers": {}', settings)
            self.assertIn('"autoMemoryEnabled": false', settings)

            mcp = Path(artifacts["mcp_config_path"]).read_text()
            self.assertIn('"mcpServers": {}', mcp)

            manifest = artifacts
            for key in (
                "enabled",
                "root",
                "settings_path",
                "mcp_config_path",
                "project_instructions_ignored",
                "plugins_disabled",
                "memory_disabled",
                "hooks_disabled",
                "skills_disabled",
            ):
                self.assertIn(key, manifest)
            self.assertTrue(manifest["project_instructions_ignored"])
            self.assertTrue(manifest["plugins_disabled"])
            self.assertTrue(manifest["memory_disabled"])
            self.assertTrue(manifest["hooks_disabled"])
            self.assertTrue(manifest["skills_disabled"])


class ClaudeArgsTests(unittest.TestCase):
    artifacts = {
        "settings_path": "/tmp/run/clean-profile/settings.json",
        "mcp_config_path": "/tmp/run/clean-profile/mcp.json",
    }

    def test_appends_native_isolation_flags(self) -> None:
        base = ["--permission-mode", "default", "--model", "haiku"]

        result = clean_profile.augment_claude_args(base, self.artifacts)

        self.assertEqual(result[:4], base)
        self.assertEqual(
            result[4:],
            [
                "--bare",
                "--disable-slash-commands",
                "--strict-mcp-config",
                "--mcp-config",
                self.artifacts["mcp_config_path"],
                "--settings",
                self.artifacts["settings_path"],
                "--setting-sources",
                "local",
            ],
        )

    def test_does_not_duplicate_or_override_existing_flags(self) -> None:
        base = [
            "--bare",
            "--disable-slash-commands",
            "--strict-mcp-config",
            "--mcp-config",
            "/old/mcp.json",
            "--settings",
            "/old/settings.json",
            "--setting-sources",
            "local",
        ]

        result = clean_profile.augment_claude_args(base, self.artifacts)

        self.assertEqual(result.count("--bare"), 1)
        self.assertEqual(
            result[result.index("--mcp-config") + 1], self.artifacts["mcp_config_path"]
        )
        self.assertEqual(
            result[result.index("--settings") + 1], self.artifacts["settings_path"]
        )


class CleanEnvTests(unittest.TestCase):
    def test_keeps_only_allowlisted_keys_and_forces_simple_mode(self) -> None:
        env = {
            "ANTHROPIC_API_KEY": "k",
            "ANTHROPIC_AUTH_TOKEN": "t",
            "ANTHROPIC_BASE_URL": "https://example.invalid",
            "PATH": "/usr/bin",
            "HOME": "/home/lzk",
            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
            "REASONING_LOOP_RUN_CONTEXT": "run-ctx-123-task-1",
            "SHOULD_DROP": "x",
        }

        cleaned = clean_profile.sanitize_env(env)

        for key in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_BASE_URL",
            "PATH",
            "HOME",
            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS",
            "REASONING_LOOP_RUN_CONTEXT",
        ):
            self.assertEqual(cleaned[key], env[key])
        self.assertEqual(cleaned["CLAUDE_CODE_SIMPLE"], "1")
        self.assertNotIn("SHOULD_DROP", cleaned)


class CanaryApplicabilityTests(unittest.TestCase):
    def test_canary_is_skipped_under_a_comm_filter(self) -> None:
        from mtagentlens_benchmark.engine.collector_manager import _tls_canary_test

        with tempfile.TemporaryDirectory() as tmp:
            diag = _tls_canary_test(Path(tmp), comm_filter="mtl-12345")
        self.assertTrue(diag.get("skipped"))
        self.assertFalse(diag.get("sent"))
        self.assertEqual(diag.get("errors"), [])
        self.assertIn("mtl-12345", diag.get("skipped_reason", ""))


class CaptureIsolationTests(unittest.TestCase):
    def test_isolated_copy_has_unique_comm_and_inode(self) -> None:
        with tempfile.NamedTemporaryFile() as real:
            with clean_profile.isolated_agent(real.name) as agent:
                self.assertIsNotNone(agent.binary)
                self.assertIsNotNone(agent.comm)
                self.assertNotEqual(agent.comm, "claude")
                self.assertFalse(Path(agent.binary).is_symlink())
                self.assertEqual(Path(agent.binary).name, agent.comm)
                self.assertLessEqual(len(agent.comm), 15)
                self.assertNotEqual(
                    os.stat(agent.binary).st_ino, os.stat(real.name).st_ino,
                    "the copy must not share the interactive session's inode",
                )

                env = agent.apply({"PATH": "/usr/bin"})
                self.assertEqual(env["MTAGENTLENS_AGENT_BINARY"], agent.binary)
                self.assertEqual(env["MTAGENTLENS_AGENT_COMM"], agent.comm)
                self.assertEqual(env["MTAGENTLENS_AGENT_COMMS"], agent.comm)

                directory = Path(agent.binary).parent
                self.assertTrue(directory.is_dir())

            self.assertFalse(directory.exists())

    def test_agent_binary_override_is_honoured_by_binary_detection(self) -> None:
        from mtagentlens_benchmark.engine.collector_manager import (
            _find_claude_binary_with_diagnostics,
        )

        with tempfile.NamedTemporaryFile(delete=False) as handle:
            handle.write(b"\x7fELF" + b"\x00" * 64)
            fake_elf = handle.name
        try:
            with mock.patch.dict(
                os.environ, {"MTAGENTLENS_AGENT_BINARY": fake_elf}, clear=False
            ):
                path, diagnostics = _find_claude_binary_with_diagnostics()
            self.assertEqual(path, fake_elf)
            self.assertEqual(diagnostics["final_path"], fake_elf)
        finally:
            os.unlink(fake_elf)


if __name__ == "__main__":
    unittest.main()
