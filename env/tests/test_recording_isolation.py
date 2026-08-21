from pathlib import Path

from mtagentlens_benchmark.engine.executor import _kill_leftover_claude
from mtagentlens_benchmark.engine.tmux_engine import _launch_task_mode


def test_recording_cleanup_removes_benchmark_tmux_and_claude(monkeypatch) -> None:
    calls: list[list[str]] = []

    class Result:
        returncode = 0
        stdout = "agtlab-deadbeef: 1 windows\nother: 1 windows\n"

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        return Result()

    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.executor.subprocess.run",
        fake_run,
    )

    _kill_leftover_claude()

    assert ["tmux", "kill-session", "-t", "agtlab-deadbeef"] in calls
    assert ["tmux", "kill-session", "-t", "other"] not in calls
    assert ["pkill", "-x", "claude"] in calls
    assert ["pkill", "-x", "claude.exe"] in calls


def test_tmux_task_mode_passes_detected_claude_binary(monkeypatch, tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        commands.append(list(cmd))
        class Result:
            returncode = 0
        return Result()

    monkeypatch.setenv("MTAGENTLENS_BENCHMARK_HOME", str(tmp_path / "env"))
    env_root = tmp_path / "env"
    (env_root / "scenarios").mkdir(parents=True)
    (env_root / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.tmux_engine.subprocess.run",
        fake_run,
    )

    _launch_task_mode(
        pane_id="agtlab-test:0.0",
        task="run task",
        log_file=tmp_path / "agent.log",
        status_file=tmp_path / "agent.json",
        env={},
        timeout_seconds=60,
        claude_bin="/opt/claude-code/claude",
        extra_claude_args=["--model", "sonnet"],
    )

    sent = commands[-1]
    assert sent[:4] == ["tmux", "send-keys", "-t", "agtlab-test:0.0"]
    assert "/opt/claude-code/claude" in sent[4]
    assert "--model sonnet" in sent[4]
