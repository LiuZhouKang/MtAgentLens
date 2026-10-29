"""Agent log identity must not depend on a reused legacy role label."""
from types import SimpleNamespace

from mtagentlens_benchmark.engine import executor
from mtagentlens_benchmark.types import AgentSpec, ScenarioConfig, ScenarioMode


def test_duplicate_role_labels_preserve_each_agents_log(tmp_path, monkeypatch):
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    scenario = ScenarioConfig(
        id="duplicate-roles", mode=ScenarioMode.AGENT_TEAMS, description="synthetic",
        agents=[AgentSpec(role_file="same-role.md", task="first"),
                AgentSpec(role_file="same-role.md", task="second")],
        fixtures_dir=fixtures,
    )

    observed_run_ids = []

    def run_agents(config, run_dir, status_dir, logs_dir, *, run_id):
        observed_run_ids.append(run_id)
        for index, agent in enumerate(config.agents):
            (logs_dir / f"agent_{index}.log").write_text(agent.task)
        return True, {"agents_total": len(config.agents)}

    monkeypatch.setattr(executor, "_run_process_mode", run_agents)
    monkeypatch.setattr(executor, "_find_claude_binary_with_diagnostics", lambda: (None, {}))
    monkeypatch.setattr(executor, "_capture_agent_artifacts", lambda *args: None)
    result = executor.execute_scenario(
        scenario, tmp_path / "run", SimpleNamespace(root=tmp_path), execution_mode="process",
    )
    assert result.success
    assert observed_run_ids == [result.run_id]
    assert set(result.task_logs) == {"agent_0", "agent_1"}
    assert [path.read_text() for path in result.task_logs.values()] == ["first", "second"]
    assert [entry["role_label"] for entry in result.summary["agent_logs"]] == [
        "same-role.md", "same-role.md",
    ]
