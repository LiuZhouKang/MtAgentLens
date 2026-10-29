from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def current_release_dataset():
    """Exercise the self-contained FuxiBench 2.0 package, not the retired tree."""
    from compare.tools.detection_scope import require_dataset_dir
    return require_dataset_dir()


@pytest.fixture
def synthetic_dataset(tmp_path):
    """Small legacy input to test serializers without claiming research results."""
    import json
    import sqlite3
    root = tmp_path / "synthetic-dataset"
    samples = []
    for sid, attack in (("attack-one", True), ("benign-one", False)):
        for name in ("scenarios", "ground_truth", "fixtures", "traces"):
            (root / name).mkdir(parents=True, exist_ok=True)
        fixture = root / "fixtures" / sid
        fixture.mkdir()
        (fixture / "docs.md").write_text("Read this task document.\n")
        scenario = {"id": sid, "mode": "single_agent", "fixture_dir": sid, "fixtures_dir": str(root / "fixtures"),
            "agents": [{"task": "Read {{FIXTURE_DIR}}/docs.md.", "role_file": "agent.md"}]}
        (root / "scenarios" / (sid + ".json")).write_text(json.dumps(scenario))
        gt = {"scenario_id": sid, "label": "attack" if attack else "benign",
            "attack_type": "B" if attack else "benign", "category": "example",
            "expected_behavior": {"should_alert": attack,
                "expected_claims": ["boundary.file./etc/shadow"] if attack else []}}
        (root / "ground_truth" / (sid + ".json")).write_text(json.dumps(gt))
        trace = root / "traces" / sid
        trace.mkdir()
        conn = sqlite3.connect(trace / "responsibility.db")
        conn.executescript("CREATE TABLE runs(run_id TEXT, final_decision TEXT, final_severity TEXT);"
            "CREATE TABLE responsibility_claims(claim_id TEXT, agent_id TEXT, claim_type TEXT, boundary_json TEXT, severity TEXT, confidence REAL);")
        conn.execute("INSERT INTO runs VALUES(?, ?, ?)", (sid, "Unsafe" if attack else "Safe", "Critical" if attack else "Info"))
        from compare.tests.fixture_helpers import capture_checkpoint
        capture_checkpoint(conn, sid)
        conn.commit()
        conn.close()
        samples.append({"scenario_id": sid, "paths": {"ground_truth": f"ground_truth/{sid}.json",
            "responsibility_db": f"traces/{sid}/responsibility.db"}})
    (root / "metadata.json").write_text(json.dumps({"scenario_ids": [s["scenario_id"] for s in samples]}))
    (root / "quality").mkdir()
    (root / "quality/manifest.json").write_text(json.dumps({"samples": samples}))
    return root


@pytest.fixture
def synthetic_paper_report(tmp_path):
    """Schema fixture only: these numbers describe no actual experiment."""
    import json
    path = tmp_path / "evaluation_report.json"
    tools = ("mtagentlens", "llm-guard", "meta-prompt-guard", "vigil")
    path.write_text(json.dumps({"schema": "mtagentlens.paper_evaluation_report.v1", "status": "completed",
        "dataset_summary": {"total_cases": 8, "attack_cases": 5, "benign_cases": 3},
        "main_metrics": [{"tool_id": tool, "f1": 0.75 - i * 0.1, "scenario_recall": 0.8 - i * 0.1}
            for i, tool in enumerate(tools)],
        "mcnemar": {tool: {"b_mtap_correct_prompt_wrong": i + 1, "c_prompt_correct_mtap_wrong": 1}
            for i, tool in enumerate(tools[1:])}}))
    return path
