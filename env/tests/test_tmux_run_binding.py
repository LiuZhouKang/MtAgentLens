"""Tmux runner evidence binds a v4 run without pretending the pane is the agent."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from mtagentlens_benchmark.engine import executor
from mtagentlens_benchmark.engine.input_reference import ReleaseInputAudit
from mtagentlens_benchmark.engine.runner import _save_summary
from mtagentlens_benchmark.loader.scenario_loader import load_scenario


def tmux_release(root: Path, *, timeout_seconds: int = 20):
    release = root / "release"
    (release / "scenarios").mkdir(parents=True)
    (release / "fixtures/neutral").mkdir(parents=True)
    (release / "bench").mkdir()
    (release / "fixtures/neutral/input.txt").write_bytes(
        b"shared fixture input\n"
    )
    (release / "bench/prepare.sh").write_text(
        'printf prepared > "$FIXTURE_DIR/setup.txt"\n'
    )
    scenario = {
        "id": "scenario",
        "mode": "agent_teams",
        "description": "Synthetic two-pane tmux check",
        "timeout": timeout_seconds,
        "fixture_dir": "neutral",
        "pre_run_script": "env/scenarios/bench/prepare.sh",
        "tmux": {"layout": "even-horizontal"},
        "agents": [
            {"role_file": "planner", "task": "Read {{FIXTURE_DIR}}/input.txt"},
            {"role_file": "reviewer", "task": "Review {{FIXTURE_DIR}}/input.txt"},
        ],
    }
    (release / "scenarios/scenario.json").write_text(json.dumps(scenario))
    from compare.tools.dataset_release import _inventory

    files = _inventory(release)
    manifest = {
        "schema": "mtagentlens.dataset-release/1",
        "files": files,
        "case_bindings": {
            "scenario": {
                "scenario_sha256": files["scenarios/scenario.json"],
                "fixture_sha256": hashlib.sha256(json.dumps(
                    _inventory(release / "fixtures/neutral"), sort_keys=True
                ).encode()).hexdigest(),
            }
        },
    }
    manifest["release_id"] = hashlib.sha256(
        json.dumps(manifest, sort_keys=True).encode()
    ).hexdigest()
    (release / "release.json").write_text(json.dumps(manifest))
    config = load_scenario(
        release / "scenarios/scenario.json", release / "fixtures"
    )
    config.pre_run_script = str(release / "bench/prepare.sh")
    return release, manifest, config


def initial_snapshot():
    return {
        "schema": "mtagentlens.kernel-process-snapshot/v1",
        "dropped": 0,
        "records": [{
            "pid": 900000,
            "start_time_ns": 123,
            "parent_pid": 0,
            "parent_start_time_ns": 0,
            "comm": "resident",
            "pgid": 4321,
            "cgroup_id": 9876543210,
            "kernel_timestamp_ns": 456,
            "monitor_pid_namespace": None,
            "monitor_pid_namespace_error": -61,
        }],
    }


class SyntheticCollector:
    def __init__(self):
        self.collector_id = str(uuid.uuid4())
        self.is_running = False
        self.database_path = None

    def set_run_context(self, scenario_id, mode):
        self.scenario_id, self.mode = scenario_id, mode

    def start(self, directory):
        directory.mkdir(parents=True)
        self.database_path = directory / "native.responsibility.db"
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "CREATE TABLE runs(run_id TEXT, scenario_id TEXT, mode TEXT, "
                "started_at_ns INTEGER, ended_at_ns INTEGER, "
                "final_decision TEXT, final_severity TEXT)"
            )
            connection.execute(
                "INSERT INTO runs VALUES(?,?,?,1,NULL,NULL,NULL)",
                (self.collector_id, self.scenario_id, self.mode),
            )
        self.is_running = True

    def stop(self):
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                "UPDATE runs SET ended_at_ns=2, final_decision='Uncertain', "
                "final_severity='Warning'"
            )
            quality = {
                "initial_process_snapshot": initial_snapshot(),
                "process_namespace_mappings": {
                    "schema": "mtagentlens.kernel-process-mappings/v2",
                    "dropped": 0,
                    "records": [],
                },
            }
            payload = {
                "run_id": self.collector_id,
                "contract": "frozen-evidence-reanalysis-v1",
                "capture_quality": quality,
            }
            connection.execute(
                "CREATE TABLE analysis_checkpoints(run_id TEXT, "
                "checkpoint_kind TEXT, version INTEGER, payload_json TEXT)"
            )
            connection.execute(
                "INSERT INTO analysis_checkpoints VALUES(?,?,1,?)",
                (
                    self.collector_id,
                    "frozen-analysis",
                    json.dumps(payload),
                ),
            )
        self.is_running = False


def fake_client(tmp_path: Path, body: str = "") -> Path:
    client = tmp_path / "client"
    client.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        + body
    )
    client.chmod(0o700)
    return client


def require_real_tmux():
    if shutil.which("tmux") is None:
        pytest.skip("tmux is not installed")
    name = f"agtlab-probe-{uuid.uuid4().hex[:8]}"
    probe = subprocess.run(
        ["tmux", "new-session", "-d", "-s", name], capture_output=True, text=True
    )
    if probe.returncode != 0:
        pytest.skip(f"tmux cannot create a session here: {probe.stderr.strip()}")
    subprocess.run(["tmux", "kill-session", "-t", name], capture_output=True)


@pytest.mark.parametrize("deescalate", [False, True])
def test_task_wrapper_keeps_run_inputs_across_environment_reset(tmp_path, deescalate):
    """Fake sudo models env_reset; this test never invokes real sudo."""
    client = fake_client(tmp_path, "import json\nprint(json.dumps({k: os.environ.get(k) for k in "
                         "('RUN_TAG', 'RUN_TAG_FILE', 'FIXTURE_DIR', 'ANTHROPIC_MODEL')}))\n")
    sudo = tmp_path / "sudo"
    sudo.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "args = sys.argv[1:]\n"
        "keep = []\n"
        "while args:\n"
        "    if args[0] == '-u': args = args[2:]\n"
        "    elif args[0].startswith('--preserve-env='):\n"
        "        keep += args.pop(0).split('=', 1)[1].split(',')\n"
        "    elif args[0] == '--': args.pop(0); break\n"
        "    else: break\n"
        "env = {k: os.environ[k] for k in keep if k in os.environ}\n"
        "env.setdefault('PATH', os.defpath)\n"
        "os.execve(args[0], args, env)\n"
    )
    sudo.chmod(0o700)
    expected = {'RUN_TAG': 'fresh-nonce', 'RUN_TAG_FILE': str(tmp_path / 'marker'),
                'FIXTURE_DIR': str(tmp_path / 'fixtures'), 'ANTHROPIC_MODEL': 'fake-model'}
    env_file = tmp_path / 'agent.env'
    env_file.write_text(''.join(f'{key}={shlex.quote(value)}\n' for key, value in expected.items()))
    env = {**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
           'MTAGENTLENS_ENV_FILE': str(env_file)}
    env.pop('MTAGENTLENS_SUDO_USER', None)
    if deescalate:
        env['MTAGENTLENS_SUDO_USER'] = 'synthetic-user'
    status, log = tmp_path / 'status.json', tmp_path / 'agent.log'
    wrapper = Path(__file__).resolve().parents[1] / 'scripts/task_wrapper.sh'
    result = subprocess.run(['bash', str(wrapper), 'harmless', str(status), str(log), '5', str(client)],
                            env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(log.read_text()) == expected


def configure_executor(tmp_path, monkeypatch, client):
    monkeypatch.setattr(
        executor, "_find_claude_binary_with_diagnostics", lambda: (str(client), {})
    )
    monkeypatch.setattr(executor, "_apply_benchmark_env", lambda *args: None)
    monkeypatch.setattr(executor, "_capture_agent_artifacts", lambda *args: None)
    monkeypatch.setattr(executor.os, "getuid", lambda: 1000)
    monkeypatch.delenv("MTAGENTLENS_CLEAN_PROFILE", raising=False)


def patch_fake_tmux(monkeypatch, *, statuses=None, launch_errors=None, create_error=None):
    statuses = statuses or {}
    launch_errors = launch_errors or {}
    calls = {"created": [], "killed": [], "launched": []}

    def fake_create(session_name, scenario, **kwargs):
        calls["created"].append(session_name)
        if create_error is not None:
            raise create_error
        return session_name

    def fake_panes(session_name):
        return [
            {"pane_index": 0, "pane_id": "%0", "pane_pid": os.getpid()},
            {"pane_index": 1, "pane_id": "%1", "pane_pid": os.getppid()},
        ]

    def fake_launch(**kwargs):
        index = kwargs["pane_index"]
        calls["launched"].append(index)
        if index in launch_errors:
            raise launch_errors[index]
        status = statuses.get(index)
        if status is not None:
            kwargs["status_file"].write_text(json.dumps(status))

    def fake_kill(session_name):
        calls["killed"].append(session_name)

    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.tmux_engine.create_tmux_session", fake_create
    )
    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.tmux_engine.observe_session_panes", fake_panes
    )
    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.tmux_engine.launch_agent_in_pane", fake_launch
    )
    monkeypatch.setattr(
        "mtagentlens_benchmark.engine.tmux_engine.kill_session", fake_kill
    )
    return calls


def run_mocked_tmux(
    tmp_path,
    monkeypatch,
    *,
    statuses=None,
    launch_errors=None,
    create_error=None,
    timeout_seconds=20,
):
    release, manifest, config = tmux_release(tmp_path, timeout_seconds=timeout_seconds)
    client = fake_client(tmp_path)
    configure_executor(tmp_path, monkeypatch, client)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)
    calls = patch_fake_tmux(
        monkeypatch,
        statuses=statuses,
        launch_errors=launch_errors,
        create_error=create_error,
    )
    collector = SyntheticCollector()
    run = tmp_path / "run"
    result = executor.execute_scenario(
        config,
        run,
        SimpleNamespace(root=tmp_path),
        execution_mode="tmux-live",
        collector=collector,
        input_audit_factory=lambda conf, directory: ReleaseInputAudit(
            release, manifest, conf, directory
        ),
    )
    _save_summary(run, result)
    frozen = run / "collector/finalized.db"
    if collector.database_path is not None and collector.database_path.exists() and not frozen.exists():
        from scripts.evaluation.run_artifacts import snapshot_database

        snapshot_database(collector.database_path, frozen)
    return {
        "release": release,
        "manifest": manifest,
        "config": config,
        "result": result,
        "run": run,
        "frozen": frozen,
        "collector": collector,
        "calls": calls,
    }


def rows_of(run: Path):
    return [json.loads(line) for line in (run / "runner-events.jsonl").read_text().splitlines()]


def test_two_pane_tmux_run_binds_v4_runner_evidence(tmp_path, monkeypatch):
    artifacts = run_mocked_tmux(
        tmp_path,
        monkeypatch,
        statuses={
            0: {"state": "done", "exit_code": 0},
            1: {"state": "done", "exit_code": 0},
        },
    )
    result = artifacts["result"]
    assert result.success, result.error
    summary = result.summary
    assert summary["source_run_uuid"]
    assert summary["runner_events_path"] == str(artifacts["run"] / "runner-events.jsonl")
    assert summary["tmux"]["agent_pid_observed"] is False
    assert summary["tmux"]["panes"][0]["pane_id"] == "%0"
    assert summary["tmux"]["panes"][1]["pane_id"] == "%1"

    rows = rows_of(artifacts["run"])
    assert {row["schema"] for row in rows} == {"mtagentlens.runner-event/v4"}
    assert rows[0]["event_type"] == "run_started"
    assert rows[-1]["event_type"] == "run_finished"
    assert rows[-1]["details"]["state"] == "completed"
    assert not [row for row in rows if row["event_type"] in ("process_started", "process_exited")]

    attempts = [row for row in rows if row["event_type"] == "launch_attempt"]
    assert len(attempts) == 2
    by_role = {row["details"]["tmux"]["role_file"]: row for row in attempts}
    assert set(by_role) == {"planner", "reviewer"}
    assert by_role["planner"]["details"]["input_snapshot"]["prompt"].startswith("Read ")
    assert by_role["reviewer"]["details"]["input_snapshot"]["prompt"].startswith("Review ")
    pane_ids = {row["details"]["tmux"]["pane_id"] for row in attempts}
    assert pane_ids == {"%0", "%1"}
    pane_pids = {row["details"]["tmux"]["pane_pid"] for row in attempts}
    assert len(pane_pids) == 2
    for row in attempts:
        tmux = row["details"]["tmux"]
        assert tmux["session"].startswith("agtlab-")
        assert tmux["pane"] == f"{tmux['session']}:0.{row['details']['agent_index']}"
        assert tmux["pane_process_key"]["pid"] == tmux["pane_pid"]
        assert tmux["pane_observation"]["schema"] == "mtagentlens.process-observation/v1"

    finished = [row for row in rows if row["event_type"] == "agent_finished"]
    assert {row["details"]["status"] for row in finished} == {"succeeded"}
    assert not [row for row in rows if row.get("process_key") is not None]

    from scripts.evaluation import run_artifacts

    binding = run_artifacts.bind_run_artifacts(
        artifacts["run"], artifacts["frozen"], artifacts["release"]
    )
    assert binding["schema"] == "mtagentlens.run-binding/v4"
    assert binding["harness_run_id"] == result.run_id
    assert binding["source_run_uuid"] == summary["source_run_uuid"]
    assert binding["collector_run_id"] == artifacts["collector"].collector_id
    assert binding["event_alignment_available"] is False
    assert binding["kernel_process_alignment"]["status"] == "unproven"
    assert binding["tmux"]["agent_pid_observed"] is False
    assert [pane["pane_id"] for pane in binding["tmux"]["panes"]] == ["%0", "%1"]
    run_artifacts.validate_run_binding(
        {
            "run_dir": str(artifacts["run"]),
            "db_path": str(artifacts["frozen"]),
            "run_id": binding["collector_run_id"],
            "scenario_id": "scenario",
            "run_binding": binding,
        },
        artifacts["release"],
    )


def test_tmux_pane_env_keeps_run_tag_and_tag_file(tmp_path, monkeypatch):
    artifacts = run_mocked_tmux(
        tmp_path,
        monkeypatch,
        statuses={
            0: {"state": "done", "exit_code": 0},
            1: {"state": "done", "exit_code": 0},
        },
    )
    env_text = (artifacts["run"] / ".agent_env").read_text()
    assert f"RUN_TAG={shlex.quote(artifacts['config'].run_marker)}" in env_text
    assert (
        f"RUN_TAG_FILE={shlex.quote(str(artifacts['run'] / 'marker.log'))}"
        in env_text
    )
    assert f"FIXTURE_DIR={shlex.quote(str(artifacts['run'] / 'fixtures'))}" in env_text
    assert f"RUN_DIR={shlex.quote(str(artifacts['run']))}" in env_text


@pytest.mark.parametrize(
    "second_status,expected",
    [
        (None, "unobserved"),
        ({"state": "done", "exit_code": 1}, "failed"),
        ({"state": "error", "error": "synthetic"}, "error"),
    ],
)
def test_missing_and_failed_status_are_not_success(
    tmp_path, monkeypatch, second_status, expected
):
    artifacts = run_mocked_tmux(
        tmp_path,
        monkeypatch,
        statuses={0: {"state": "done", "exit_code": 0}, 1: second_status},
        timeout_seconds=0 if second_status is None else 20,
    )
    assert artifacts["result"].success is False
    ledger = json.loads((artifacts["run"] / "runner-ledger.json").read_text())
    assert ledger["summary"]["statuses"][expected] == 1
    finished = {
        row["details"]["status"]
        for row in rows_of(artifacts["run"])
        if row["event_type"] == "agent_finished"
    }
    assert expected in finished


def test_launch_exception_is_recorded_and_session_is_always_killed(tmp_path, monkeypatch):
    artifacts = run_mocked_tmux(
        tmp_path,
        monkeypatch,
        statuses={0: {"state": "done", "exit_code": 0}},
        launch_errors={1: OSError("synthetic send-keys failure")},
    )
    assert artifacts["result"].success is False
    session = artifacts["calls"]["created"][0]
    assert artifacts["calls"]["killed"] == [session]
    rows = rows_of(artifacts["run"])
    types = [row["event_type"] for row in rows]
    assert "launch_failed" in types
    assert rows[-1]["event_type"] == "run_finished"
    assert rows[-1]["details"]["state"] == "completed"
    ledger = json.loads((artifacts["run"] / "runner-ledger.json").read_text())
    assert ledger["summary"]["statuses"]["error"] == 1
    assert ledger["summary"]["statuses"]["succeeded"] == 1


def test_session_creation_exception_still_kills_and_freezes_interrupted_scope(
    tmp_path, monkeypatch
):
    artifacts = run_mocked_tmux(
        tmp_path,
        monkeypatch,
        create_error=OSError("synthetic tmux missing"),
    )
    assert artifacts["result"].success is False
    assert artifacts["calls"]["killed"] == artifacts["calls"]["created"]
    rows = rows_of(artifacts["run"])
    assert [row["event_type"] for row in rows] == ["run_started", "run_finished"]
    assert rows[-1]["details"]["state"] == "interrupted"


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_real_two_pane_fake_agent_records_distinct_panes_and_run_tag(
    tmp_path, monkeypatch
):
    require_real_tmux()
    release, manifest, config = tmux_release(tmp_path, timeout_seconds=20)
    client = fake_client(
        tmp_path,
        "tag_file = os.environ.get('RUN_TAG_FILE')\n"
        "if tag_file:\n"
        "    with open(tag_file, 'a', encoding='utf-8') as handle:\n"
        "        handle.write(os.environ.get('RUN_TAG', '') + '\\n')\n"
        "    Path(tag_file).with_name(f'cwd-{os.getpid()}.txt').write_text(os.getcwd())\n"
        "print('harmless')\n",
    )
    configure_executor(tmp_path, monkeypatch, client)
    real_sleep = time.sleep
    monkeypatch.setattr(
        executor.time,
        "sleep",
        lambda seconds: None if seconds >= 2 else real_sleep(seconds),
    )
    collector = SyntheticCollector()
    run = tmp_path / "run"
    session = None
    try:
        result = executor.execute_scenario(
            config,
            run,
            SimpleNamespace(root=tmp_path),
            execution_mode="tmux-live",
            collector=collector,
            input_audit_factory=lambda conf, directory: ReleaseInputAudit(
                release, manifest, conf, directory
            ),
        )
        assert result.success, result.error
        rows = rows_of(run)
        attempts = [row for row in rows if row["event_type"] == "launch_attempt"]
        assert len(attempts) == 2
        session = attempts[0]["details"]["tmux"]["session"]
        assert len({row["details"]["tmux"]["pane_id"] for row in attempts}) == 2
        assert len({row["details"]["tmux"]["pane_pid"] for row in attempts}) == 2
        assert {row["details"]["tmux"]["role_file"] for row in attempts} == {
            "planner",
            "reviewer",
        }
        cwd_files = sorted(run.glob("cwd-*.txt"))
        assert len(cwd_files) == 2
        assert {path.read_text() for path in cwd_files} == {str(run.resolve())}
        for row in attempts:
            assert Path(row["details"]["input_snapshot"]["cwd"]).resolve() == run.resolve()
        assert (run / "marker.log").read_text().splitlines() == [
            config.run_marker,
            config.run_marker,
        ]
        assert subprocess.run(
            ["tmux", "has-session", "-t", session], capture_output=True
        ).returncode != 0
    finally:
        if session is not None:
            subprocess.run(
                ["tmux", "kill-session", "-t", session], capture_output=True
            )


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_real_tmux_timeout_is_not_success_and_cleans_up(tmp_path, monkeypatch):
    require_real_tmux()
    release, manifest, config = tmux_release(tmp_path, timeout_seconds=1)
    client = fake_client(tmp_path, "time.sleep(30)\n")
    configure_executor(tmp_path, monkeypatch, client)
    real_sleep = time.sleep
    monkeypatch.setattr(
        executor.time,
        "sleep",
        lambda seconds: None if seconds >= 2 else real_sleep(seconds),
    )
    collector = SyntheticCollector()
    run = tmp_path / "run"
    session = None
    try:
        result = executor.execute_scenario(
            config,
            run,
            SimpleNamespace(root=tmp_path),
            execution_mode="tmux-live",
            collector=collector,
            input_audit_factory=lambda conf, directory: ReleaseInputAudit(
                release, manifest, conf, directory
            ),
        )
        assert result.success is False
        rows = rows_of(run)
        session = next(
            row["details"]["tmux"]["session"]
            for row in rows
            if row["event_type"] == "launch_attempt"
        )
        assert subprocess.run(
            ["tmux", "has-session", "-t", session], capture_output=True
        ).returncode != 0
    finally:
        if session is not None:
            subprocess.run(
                ["tmux", "kill-session", "-t", session], capture_output=True
            )


def test_send_keys_argv_and_pane_cwd_match_the_launch_audit(tmp_path, monkeypatch):
    from mtagentlens_benchmark.engine import tmux_engine
    from mtagentlens_benchmark.types import AgentSpec, ScenarioConfig, ScenarioMode

    run = tmp_path / "run"
    for name in ("logs", "status", "fixtures"):
        (run / name).mkdir(parents=True)
    client = fake_client(
        tmp_path,
        "Path(os.environ['RUN_TAG_FILE']).with_name("
        "f'cwd-{os.getpid()}.txt').write_text(os.getcwd())\n",
    )
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "synthetic-secret-token")
    monkeypatch.setattr(executor, "_apply_benchmark_env", lambda *args: None)
    monkeypatch.setattr(executor, "_capture_agent_artifacts", lambda *args: None)
    monkeypatch.setattr(executor.os, "getuid", lambda: 1000)
    monkeypatch.delenv("MTAGENTLENS_CLEAN_PROFILE", raising=False)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)
    config = ScenarioConfig(
        id="scenario", mode=ScenarioMode.AGENT_TEAMS, description="argv test",
        agents=[
            AgentSpec(role_file="planner", task="harmless one"),
            AgentSpec(role_file="reviewer", task="harmless two"),
        ],
        fixtures_dir=run / "fixtures", timeout_seconds=2,
        run_marker="mtl-argv-test",
    )

    real_run = subprocess.run
    recorded = []
    cwd = {"value": None}

    class Result:
        def __init__(self, returncode=0, stdout=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def fake_run(argv, *args, **kwargs):
        recorded.append(list(argv))
        if argv[:2] == ["tmux", "kill-session"]:
            return Result(1)
        if argv[:3] == ["tmux", "new-session", "-d"]:
            cwd["value"] = Path(argv[argv.index("-c") + 1])
            return Result()
        if argv[:2] == ["tmux", "split-window"]:
            return Result()
        if argv[:2] == ["tmux", "select-layout"]:
            return Result()
        if argv[:2] == ["tmux", "list-panes"]:
            return Result(stdout=f"0\t%0\t{os.getpid()}\n1\t%1\t{os.getppid()}\n")
        if argv[:3] == ["tmux", "send-keys", "-t"]:
            completed = real_run(
                ["bash", "-c", argv[4]], cwd=str(cwd["value"]),
                env=os.environ.copy(), stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=30,
            )
            assert completed.returncode == 0
            return Result()
        raise AssertionError(argv)

    monkeypatch.setattr(tmux_engine.subprocess, "run", fake_run)
    success, _summary = executor._run_tmux_mode(
        config, run, "tmux-live", run / "status", run / "logs", str(client),
        run_id="run-argv",
    )
    assert success

    rows = rows_of(run)
    attempts = [row for row in rows if row["event_type"] == "launch_attempt"]
    assert len(attempts) == 2
    send_keys = [argv for argv in recorded if argv[:3] == ["tmux", "send-keys", "-t"]]
    assert len(send_keys) == 2
    by_pane = {row["details"]["tmux"]["pane"]: row for row in attempts}
    for argv in send_keys:
        assert by_pane[argv[3]]["details"]["input_snapshot"]["command"] == argv
        assert "task_wrapper.sh" in argv[4]
        assert "--model" in argv[4] and "haiku" in argv[4]
        assert "--effort" in argv[4] and "low" in argv[4]
        assert (
            f"MTAGENTLENS_ENV_FILE={shlex.quote(str(run / '.agent_env'))}"
            in argv[4]
        )
        assert "ANTHROPIC_AUTH_TOKEN" not in argv[4]

    new_session = next(
        argv for argv in recorded if argv[:3] == ["tmux", "new-session", "-d"]
    )
    assert Path(new_session[new_session.index("-c") + 1]).resolve() == run.resolve()
    splits = [argv for argv in recorded if argv[:2] == ["tmux", "split-window"]]
    assert splits
    for argv in splits:
        assert Path(argv[argv.index("-c") + 1]).resolve() == run.resolve()

    for row in attempts:
        snapshot = row["details"]["input_snapshot"]
        assert Path(snapshot["cwd"]).resolve() == run.resolve()
        assert "synthetic-secret-token" not in json.dumps(snapshot)
    cwd_files = sorted(run.glob("cwd-*.txt"))
    assert len(cwd_files) == 2
    assert {path.read_text() for path in cwd_files} == {str(run.resolve())}


def test_canonical_multi_input_with_real_tmux_and_mock_agent(tmp_path, monkeypatch):
    """Canonical inputs and real panes; mock collector is not eBPF evidence."""
    require_real_tmux()
    release = Path(__file__).resolve().parents[2] / "datasets/FuxiBench"
    manifest = json.loads((release / "release.json").read_text())
    config = load_scenario(
        release / "scenarios/benign-multi-handoff.json", release / "fixtures"
    )
    assert len(config.agents) == 3
    assert config.pre_run_script is None
    client = fake_client(
        tmp_path,
        "tag_file = Path(os.environ['RUN_TAG_FILE'])\n"
        "with tag_file.open('a') as handle:\n"
        "    handle.write(os.environ['RUN_TAG'] + '\\n')\n"
        "print('mock canonical workload: no model or task execution')\n",
    )
    configure_executor(tmp_path, monkeypatch, client)
    real_sleep = time.sleep
    monkeypatch.setattr(executor.time, "sleep", lambda n: None if n >= 2 else real_sleep(n))
    run = tmp_path / "canonical-mock-run"
    result = executor.execute_scenario(
        config, run, SimpleNamespace(root=tmp_path), execution_mode="tmux-live",
        collector=SyntheticCollector(),
        input_audit_factory=lambda conf, directory: ReleaseInputAudit(
            release, manifest, conf, directory
        ),
    )
    assert result.success, result.error
    rows = rows_of(run)
    launches = [row for row in rows if row["event_type"] == "launch_attempt"]
    assert len(launches) == 3
    assert len({row["details"]["tmux"]["pane_pid"] for row in launches}) == 3
    assert len({row["details"]["tmux"]["pane_id"] for row in launches}) == 3
    started = next(row for row in rows if row["event_type"] == "run_started")
    assert started["details"]["input_reference"]["release_id"] == manifest["release_id"]
    for row in launches:
        assert row["details"]["tmux"]["role_file"]
    assert (run / "marker.log").read_text().splitlines() == [config.run_marker] * 3
    assert len(list((run / "logs").glob("*"))) >= 3
