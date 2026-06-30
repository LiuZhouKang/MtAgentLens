import json
from pathlib import Path

from mtagentlens_benchmark.engine.executor import _apply_benchmark_env


def _write_benchmark_env(env_root: Path) -> None:
    config_dir = env_root / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "claude.env").write_text(
        "ANTHROPIC_BASE_URL=https://example.invalid/anthropic\n",
        encoding="utf-8",
    )
    (env_root / "scenarios").mkdir()
    (env_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")


def test_benchmark_env_keeps_claude_hooks_disabled_by_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    env_root = tmp_path / "env"
    _write_benchmark_env(env_root)
    home = tmp_path / "home"
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True)
    (claude_dir / "settings.json").write_text(
        json.dumps({"hooks": {"SessionStart": [{"matcher": "", "hooks": []}]}}),
        encoding="utf-8",
    )

    monkeypatch.setenv("MTAGENTLENS_BENCHMARK_HOME", str(env_root))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("MTAGENTLENS_ENABLE_CLAUDE_HOOKS", raising=False)

    env: dict[str, str] = {}
    _apply_benchmark_env(env, tmp_path / "run")

    settings = json.loads(
        (tmp_path / "run" / ".claude_benchmark" / "settings.json").read_text(
            encoding="utf-8",
        )
    )
    assert "hooks" not in settings


def test_benchmark_env_can_copy_claude_hooks_for_pixel_agents_recording(
    tmp_path: Path,
    monkeypatch,
) -> None:
    env_root = tmp_path / "env"
    _write_benchmark_env(env_root)
    home = tmp_path / "home"
    claude_dir = home / ".claude"
    claude_dir.mkdir(parents=True)
    hooks = {
        "SessionStart": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": "node /tmp/claude-hook.js",
                        "timeout": 5,
                    }
                ],
            }
        ],
        "SubagentStart": [{"matcher": "", "hooks": []}],
    }
    (claude_dir / "settings.json").write_text(
        json.dumps({"hooks": hooks, "enabledPlugins": {"other": True}}),
        encoding="utf-8",
    )

    monkeypatch.setenv("MTAGENTLENS_BENCHMARK_HOME", str(env_root))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("MTAGENTLENS_ENABLE_CLAUDE_HOOKS", "1")

    env: dict[str, str] = {}
    _apply_benchmark_env(env, tmp_path / "run")

    settings = json.loads(
        (tmp_path / "run" / ".claude_benchmark" / "settings.json").read_text(
            encoding="utf-8",
        )
    )
    assert settings["hooks"] == hooks
    assert settings["enabledPlugins"] == {}
