"""A stale collection sink silently removes the completion witness."""

from mtagentlens_benchmark.engine.executor import is_leftover_sink_cmdline


def test_release_style_sink_cmdline_is_recognised():
    assert is_leftover_sink_cmdline(
        "python3 /proj/release/bench/c2-beacon-direct/../sink.py")


def test_repo_style_sink_cmdline_is_recognised():
    assert is_leftover_sink_cmdline(
        "python3 /proj/env/scenarios/bench/sink.py")


def test_an_unrelated_sink_is_not_killed():
    assert not is_leftover_sink_cmdline("python3 /opt/other/sink.py")
    assert not is_leftover_sink_cmdline("python3 /proj/release/bench/runner.py")


def test_a_shell_that_merely_mentions_a_sink_is_not_killed():
    # The campaign command line often contains this string; matching it would
    # kill the caller (observed while testing this very helper).
    assert not is_leftover_sink_cmdline(
        "bash -c 'python3 /proj/env/scenarios/bench/sink.py --help'")


def test_post_run_cleanup_runs_without_a_pre_run_script(tmp_path, monkeypatch):
    """A scenario that starts its own sink must still be cleaned up after the run."""
    from types import SimpleNamespace

    from mtagentlens_benchmark.engine import executor
    from mtagentlens_benchmark.types import AgentSpec, ScenarioConfig, ScenarioMode

    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    scenario = ScenarioConfig(
        id="sink-started-by-scenario", mode=ScenarioMode.SINGLE_AGENT,
        description="synthetic", agents=[AgentSpec(role_file="developer.md", task="task")],
        fixtures_dir=fixtures,
    )
    calls = []

    def fake_process_mode(config, run_dir, status_dir, logs_dir, *, run_id):
        return True, {"agents_total": 1}

    monkeypatch.setattr(executor, "_run_process_mode", fake_process_mode)
    monkeypatch.setattr(executor, "_find_claude_binary_with_diagnostics", lambda: (None, {}))
    monkeypatch.setattr(executor, "_capture_agent_artifacts", lambda *args: None)
    monkeypatch.setattr(executor, "sink_receipt_inventory", lambda scenario_id: [])
    monkeypatch.setattr(executor, "_copy_sink_receipts", lambda *args: [])
    monkeypatch.setattr(executor, "stop_leftover_sinks", lambda: calls.append("stop") or 2)
    result = executor.execute_scenario(
        scenario, tmp_path / "run", SimpleNamespace(root=tmp_path), execution_mode="process",
    )
    assert result.success
    assert calls == ["stop"], "post-run cleanup must run even without a pre_run_script"
    assert result.summary["leftover_sinks_stopped"] == 2


def test_same_process_group_sink_is_stopped(tmp_path):
    """A sink started by the agent lives in the executor's process group."""
    import subprocess
    import sys
    import time

    from mtagentlens_benchmark.engine.executor import stop_leftover_sinks

    fake_root = tmp_path / "FuxiBench" / "bench" / "scenario"
    fake_root.mkdir(parents=True)
    script = fake_root / "sink.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    process = subprocess.Popen([sys.executable, str(script)])  # same process group
    try:
        time.sleep(0.3)
        assert process.poll() is None
        stopped = stop_leftover_sinks()
        assert stopped >= 1, "the same-group sink must not be skipped"
        process.wait(timeout=5)
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            process.kill()
