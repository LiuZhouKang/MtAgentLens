# SPDX-License-Identifier: Apache-2.0 OR MIT
# Copyright (c) 2026 MtAgentLens Project
"""Clean-profile isolation for benchmark agent runs.

The measurement harness must answer "what did *this scenario's agent* do",
not "what did every Claude process on the machine do". Two independent
surfaces have to be isolated:

1. **Agent configuration** — user plugins, MCP servers, hooks, skills, memory
   and project instructions must not leak into the scenario. This module
   creates an empty ``settings.json`` / ``mcp.json`` per run and returns the
   Claude CLI flags and environment allowlist that pin the agent to them.
2. **Capture surface** — the collector must not see the interactive session
   that is driving the benchmark. That is handled outside this module: the
   runner launches the agent through a uniquely named symlink and passes the
   same name as the collector's ``comm`` filter (see ``isolated_agent``).

The contract is recorded in a per-run ``manifest.json`` so a result can be
audited after the fact.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# Environment keys the agent may inherit. Everything else is dropped: a
# benchmark run must not inherit the operator's shell, hooks or tool config.
ENV_ALLOWLIST = {
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_REASONING_MODEL",
    "MODEL",
    "API_TIMEOUT_MS",
    "LANGUAGE",
    "PATH",
    "HOME",
    "USER",
    "SHELL",
    "LOGNAME",
    "TERM",
    "LANG",
    "LC_ALL",
    "TMPDIR",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS",
    "CLAUDE_SUBAGENTS_MODE",
    "TEAMMATE_MODE",
    "REASONING_LOOP_RUN_CONTEXT",
    "MTAGENTLENS_SUDO_USER",
}

CLAUDE_FLAGS = (
    "--bare",
    "--disable-slash-commands",
    "--strict-mcp-config",
)


def enabled() -> bool:
    """Whether the clean profile is active (default off for compatibility)."""
    return os.environ.get("MTAGENTLENS_CLEAN_PROFILE", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def create_artifacts(run_dir: Path) -> dict:
    """Write the per-run clean settings/MCP files and return their paths."""
    root = (run_dir / "clean-profile").resolve()
    settings_path = root / "settings.json"
    mcp_config_path = root / "mcp.json"
    root.mkdir(parents=True, exist_ok=True)

    settings = {
        "enabledPlugins": {},
        "hooks": {},
        "includeCoAuthoredBy": False,
        "autoMemoryEnabled": False,
        "mcpServers": {},
    }
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")
    mcp_config_path.write_text(
        json.dumps({"mcpServers": {}}, indent=2) + "\n", encoding="utf-8"
    )

    manifest = {
        "enabled": True,
        "root": str(root),
        "settings_path": str(settings_path),
        "mcp_config_path": str(mcp_config_path),
        "project_instructions_ignored": True,
        "plugins_disabled": True,
        "memory_disabled": True,
        "hooks_disabled": True,
        "skills_disabled": True,
        "flags": list(CLAUDE_FLAGS),
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def _has_flag(args: list[str], flag: str) -> bool:
    for token in args:
        if token == "--":
            return False
        if token == flag or token.startswith(flag + "="):
            return True
    return False


def _set_flag(args: list[str], flag: str, value: str) -> None:
    for idx, token in enumerate(args):
        if token == "--":
            break
        if token == flag:
            if idx + 1 < len(args):
                args[idx + 1] = value
            else:
                args.append(value)
            return
        if token.startswith(flag + "="):
            args[idx] = f"{flag}={value}"
            return
    args.extend([flag, value])


def augment_claude_args(base_args: list[str], artifacts: dict) -> list[str]:
    """Return ``base_args`` plus the flags that pin the clean profile."""
    args = list(base_args)
    for flag in CLAUDE_FLAGS:
        if not _has_flag(args, flag):
            args.append(flag)
    _set_flag(args, "--mcp-config", str(artifacts["mcp_config_path"]))
    _set_flag(args, "--settings", str(artifacts["settings_path"]))
    _set_flag(args, "--setting-sources", "local")
    return args


def sanitize_env(env: dict[str, str]) -> dict[str, str]:
    """Keep only the allowlist and force the non-interactive simple mode."""
    cleaned = {key: value for key, value in env.items() if key in ENV_ALLOWLIST}
    cleaned["CLAUDE_CODE_SIMPLE"] = "1"
    cleaned["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return cleaned


# ── Capture-surface isolation ─────────────────────────────────────────


def resolve_real_agent_binary() -> str | None:
    """Resolve the real Claude ELF without importing the executor (cycle)."""
    from mtagentlens_benchmark.engine.collector_manager import (
        _find_claude_binary_with_diagnostics,
    )

    path, _diag = _find_claude_binary_with_diagnostics()
    return path


class isolated_agent:
    """Context manager that exposes the agent under a unique ``comm``.

    A symlink whose basename is unique is exec'd by the scenario. The kernel
    sets ``comm`` from that basename, so the collector's ``-c`` filter (and
    the TLS uprobe's comm check) exclude the interactive session that is
    driving the benchmark.

    Usage::

        with isolated_agent() as agent:
            env = agent.apply(os.environ.copy())
            # launch ``agent.binary`` with ``agent.comm``
    """

    def __init__(self, real_binary: str | None = None, prefix: str = "mtl"):
        self._real = real_binary or resolve_real_agent_binary()
        self._prefix = prefix
        self._dir: Path | None = None
        self.binary: str | None = None
        self.comm: str | None = None

    def __enter__(self) -> "isolated_agent":
        if not self._real:
            return self
        self._dir = Path(tempfile.mkdtemp(prefix="mtl-isolated-agent-"))
        # Keep the name <= 15 bytes: the kernel truncates TASK_COMM_LEN and a
        # truncated name would never match the collector's exact comm filter.
        self.comm = f"{self._prefix}-{os.getpid()}"
        target = self._dir / self.comm
        # A COPY, not a symlink: uprobes attach per inode, and a symlink shares
        # the interactive session's inode. Only a distinct inode keeps the
        # session's TLS traffic out of the measurement.
        try:
            subprocess.run(
                ["cp", "--reflink=auto", self._real, str(target)],
                check=True, capture_output=True,
            )
        except Exception:
            shutil.copy2(self._real, target)
        target.chmod(0o755)
        self.binary = str(target)
        return self

    def apply(self, env: dict[str, str]) -> dict[str, str]:
        """Point the benchmark and collector at the isolated executable."""
        if not self.binary or not self.comm:
            return env
        env = dict(env)
        env["MTAGENTLENS_AGENT_BINARY"] = self.binary
        env["MTAGENTLENS_AGENT_COMM"] = self.comm
        env["MTAGENTLENS_AGENT_COMMS"] = self.comm
        return env

    def __exit__(self, *_exc) -> None:
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None
