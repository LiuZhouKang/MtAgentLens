"""Index native top-level launch evidence, without assigning internal agents.

Producer/actor names are runner declarations, not authenticated identities.
Process exit codes do not establish attack attempt, execution or success.
V2/v3/v4 execution-source references verify source bytes, path rewrites and the
first prepared launch. A v4 ledger also brackets every launch with a runner
self-context sample; the two endpoints cannot prove clock continuity between
them. Without that reference the release binding is declarative.
Dynamic setup output is observed; this does not attest a complete environment.
"""

import base64
import binascii
import hashlib
import json
import posixpath
import re
import shutil
import tempfile
import uuid
from pathlib import Path, PurePosixPath

from tools.annotator.evidence import _loads_strict, build_bundle
from compare.tools.dataset_release import validate_release

_KEYS = {"schema", "producer", "source_run_uuid", "run_id", "scenario_id", "event_id",
         "event_type", "timestamp_ns", "clock_domain", "actor_id", "process_key", "details"}
_TYPES = {"run_started", "launch_attempt", "process_started", "process_exited",
          "process_timeout", "launch_failed", "agent_finished", "run_finished"}
_TMUX_STATUSES = {"succeeded", "failed", "timeout", "error", "unobserved"}
_V1 = "mtagentlens.runner-event/v1"
_V2 = "mtagentlens.runner-event/v2"
_V3 = "mtagentlens.runner-event/v3"
_V4 = "mtagentlens.runner-event/v4"


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _uuid(value, *, hex_only=False):
    _require(isinstance(value, str), "UUID must be a string")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise ValueError("invalid native UUID") from exc
    _require((parsed.hex if hex_only else str(parsed)) == value, "UUID is not canonical")


def _process_key(key):
    _require(isinstance(key, dict) and set(key) ==
             {"pid", "start_ticks", "boot_id", "ticks_per_second"}, "invalid process key")
    _require(type(key["pid"]) is int and 0 < key["pid"] <= 0x7FFFFFFF, "invalid process pid")
    _require(key["start_ticks"] is None or
             (type(key["start_ticks"]) is int and key["start_ticks"] >= 0), "invalid start ticks")
    _require(type(key["ticks_per_second"]) is int and key["ticks_per_second"] > 0,
             "invalid process clock frequency")
    if key["boot_id"] is not None:
        _uuid(key["boot_id"])


def _observation_space(value):
    nspid = value["nspid"]
    _require(nspid is None or (isinstance(nspid, list) and nspid and
             all(type(part) is int and part > 0 for part in nspid)), "invalid namespace pid chain")
    spaces = value["namespaces"]
    _require(isinstance(spaces, dict) and set(spaces) ==
             {"pid", "pid_for_children", "time", "time_for_children", "mnt"}, "invalid namespaces")
    for ns in spaces.values():
        _require(ns is None or (isinstance(ns, dict) and set(ns) == {"device", "inode"} and
                 type(ns["device"]) is int and ns["device"] >= 0 and
                 type(ns["inode"]) is int and ns["inode"] > 0), "invalid namespace identity")
    offsets = value["time_offsets_ns"]
    _require(isinstance(offsets, dict) and set(offsets) == {"monotonic", "boottime"} and
             all(offset is None or type(offset) is int for offset in offsets.values()),
             "invalid time namespace offsets")


def _observer_entity(value):
    _require(isinstance(value, dict) and set(value) ==
             {"pid", "procfs_pid", "nspid", "namespaces", "time_offsets_ns"},
             "invalid process observer fields")
    _require(type(value["pid"]) is int and value["pid"] > 0, "invalid observer pid")
    _require(value["procfs_pid"] is None or
             (type(value["procfs_pid"]) is int and value["procfs_pid"] > 0), "invalid procfs pid")
    _observation_space(value)


def _target_entity(value, key):
    _require(isinstance(value, dict) and set(value) ==
             {"procfs_pid", "start_ticks_before", "start_ticks_after", "nspid",
              "namespaces", "time_offsets_ns"}, "invalid process context fields")
    _require(type(value["procfs_pid"]) is int and value["procfs_pid"] == key["pid"],
             "target procfs pid does not match process key")
    for name in ("start_ticks_before", "start_ticks_after"):
        _require(value[name] is None or (type(value[name]) is int and value[name] >= 0),
                 "invalid observed start ticks")
    _observation_space(value)


def _process_observation(sample, key, *, lower, upper):
    _require(isinstance(sample, dict) and set(sample) == {
        "schema", "sample_before_ns", "sample_after_ns", "boottime_ns", "status", "observer", "target"
    }, "invalid process observation fields")
    _require(sample["schema"] == "mtagentlens.process-observation/v1", "unsupported process observation")
    before, after = sample["sample_before_ns"], sample["sample_after_ns"]
    _require(type(before) is int and type(after) is int and lower <= before <= after <= upper,
             "process observation outside launch bracket")
    _require(sample["boottime_ns"] is None or
             (type(sample["boottime_ns"]) is int and sample["boottime_ns"] >= 0), "invalid boottime sample")
    status = sample["status"]
    _require(status in ("observed", "unavailable", "unstable", "pid_space_mismatch"),
             "invalid process observation status")

    observer, target = sample["observer"], sample["target"]
    _observer_entity(observer)
    same_space = observer["pid"] == observer["procfs_pid"] and observer["nspid"] == [observer["pid"]]
    if target is not None:
        _require(same_space, "target read under unverified procfs pid space")
        _target_entity(target, key)
    if status == "observed":
        _require(same_space and target is not None and key["start_ticks"] is not None and key["start_ticks"] > 0 and
                 target["start_ticks_before"] == target["start_ticks_after"] == key["start_ticks"] and
                 target["nspid"] is not None and target["nspid"][0] == key["pid"],
                 "observed process identity is inconsistent")
    else:
        _require(key["start_ticks"] is None, "unavailable observation claims stable process identity")
    if status == "pid_space_mismatch":
        _require(target is None and ((observer["procfs_pid"] is not None and
                 observer["pid"] != observer["procfs_pid"]) or
                 (observer["nspid"] is not None and observer["nspid"] != [observer["pid"]])),
                 "pid space mismatch lacks contradictory evidence")


def _runner_context(value, *, attempt_time):
    _require(isinstance(value, dict) and set(value) == {
        "schema", "sample_before_ns", "sample_after_ns", "boottime_ns", "boot_id", "status", "observer"
    }, "invalid runner context fields")
    _require(value["schema"] == "mtagentlens.runner-context/v1", "unsupported runner context")
    before, after = value["sample_before_ns"], value["sample_after_ns"]
    _require(type(before) is int and type(after) is int and 0 <= before <= after <= attempt_time,
             "runner context outside the pre-launch bracket")
    _require(value["boottime_ns"] is None or
             (type(value["boottime_ns"]) is int and value["boottime_ns"] >= 0),
             "invalid runner boottime sample")
    if value["boot_id"] is not None:
        _uuid(value["boot_id"])
    status = value["status"]
    _require(status in ("observed", "unavailable", "pid_space_mismatch"),
             "invalid runner context status")
    observer = value["observer"]
    _observer_entity(observer)
    same_space = observer["pid"] == observer["procfs_pid"] and observer["nspid"] == [observer["pid"]]
    if status == "observed":
        _require(same_space, "observed runner context crosses the procfs pid space")
    if status == "pid_space_mismatch":
        _require((observer["procfs_pid"] is not None and observer["pid"] != observer["procfs_pid"]) or
                 (observer["nspid"] is not None and observer["nspid"] != [observer["pid"]]),
                 "runner pid space mismatch lacks contradictory evidence")


def _tmux_launch(value, agent_index, attempt_time):
    """Typed pane metadata; the pane pid is explicitly not the agent pid."""
    _require(isinstance(value, dict) and set(value) == {
        "session", "pane", "pane_id", "pane_pid", "pane_process_key",
        "pane_observation", "role_file"}, "invalid tmux launch details")
    _require(_text(value["session"]) and _text(value["pane"]),
             "invalid tmux session or pane")
    _require(value["pane"] == f"{value['session']}:0.{agent_index}",
             "tmux pane does not match the configured invocation")
    _require(value["pane_id"] is None or _text(value["pane_id"]),
             "invalid tmux pane id")
    _require(isinstance(value["role_file"], str), "invalid tmux role label")
    if value["pane_pid"] is None:
        _require(value["pane_process_key"] is None and value["pane_observation"] is None,
                 "tmux pane pid missing but process context present")
        return
    _require(type(value["pane_pid"]) is int and value["pane_pid"] > 0,
             "invalid tmux pane pid")
    key = value["pane_process_key"]
    _process_key(key)
    _require(key["pid"] == value["pane_pid"],
             "tmux pane pid does not match its process key")
    _process_observation(
        value["pane_observation"], key, lower=0, upper=attempt_time,
    )


def _launch_inputs(details):
    snapshot = details["input_snapshot"]
    _require(isinstance(snapshot, dict) and set(snapshot) ==
             {"schema", "cwd", "prompt", "claude_args", "command", "fixtures"},
             "invalid launch input snapshot fields")
    _require(snapshot["schema"] == "mtagentlens.launch-inputs/v1", "unsupported launch inputs")
    _require(_text(snapshot["cwd"]) and PurePosixPath(snapshot["cwd"]).is_absolute(),
             "launch cwd must be absolute")
    _require(isinstance(snapshot["prompt"], str), "invalid actual prompt")
    for name in ("claude_args", "command"):
        _require(isinstance(snapshot[name], list) and snapshot[name] and
                 all(isinstance(arg, str) for arg in snapshot[name]), "invalid launch argv")
    args = snapshot["claude_args"]
    _require(len(args) >= 2 and args[0] == "-p" and args[1] == snapshot["prompt"],
             "actual prompt does not match client argv")
    tree = snapshot["fixtures"]
    if tree is not None:
        _require(isinstance(tree, dict) and set(tree) == {"files", "directories"} and
                 isinstance(tree["files"], dict) and isinstance(tree["directories"], dict),
                 "invalid fixture input tree")
        files, directories = tree["files"], tree["directories"]
        _require("." in directories and not set(files) & set(directories),
                 "missing fixture root or conflicting paths")
        for name, entries in (("files", files), ("directories", directories)):
            for relative, value in entries.items():
                _require(isinstance(relative, str) and relative and "\x00" not in relative,
                         "invalid fixture path")
                path = PurePosixPath(relative)
                _require(not path.is_absolute() and ".." not in path.parts and
                         path.as_posix() == relative and (relative != "." or name == "directories"),
                         "escaping or noncanonical fixture path")
                if relative != ".":
                    _require(path.parent.as_posix() in directories, "missing fixture parent directory")
                if name == "files":
                    _require(isinstance(value, dict) and set(value) ==
                             {"mode", "content_base64", "sha256"}, "invalid fixture file fields")
                    encoded = value["content_base64"]
                    _require(isinstance(encoded, str), "invalid fixture bytes")
                    try:
                        raw = base64.b64decode(encoded, validate=True)
                    except (ValueError, binascii.Error) as exc:
                        raise ValueError("invalid fixture base64 bytes") from exc
                    _require(base64.b64encode(raw).decode("ascii") == encoded and
                             hashlib.sha256(raw).hexdigest() == value["sha256"],
                             "fixture input checksum mismatch")
                    mode = value["mode"]
                else:
                    mode = value
                _require(type(mode) is int and 0 <= mode <= 0o7777, "invalid fixture mode")
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False,
                                      separators=(",", ":")).encode()).hexdigest()
    _require(digest == details["input_sha256"], "launch input snapshot checksum mismatch")


def _transition(row, states):
    kind, actor, key, details = (row[name] for name in
                                ("event_type", "actor_id", "process_key", "details"))
    if kind in ("run_started", "run_finished"):
        _require(actor is None and key is None, "run scope records cannot identify an actor")
        if kind == "run_started":
            _require(details == {} or (row["schema"] in (_V2, _V3, _V4) and
                     set(details) == {"input_reference"} and isinstance(details["input_reference"], dict)),
                     "invalid run start details")
        else:
            _require(set(details) == {"state"} and details["state"] in ("completed", "interrupted"),
                     "invalid run finish state")
            if details["state"] == "completed":
                _require(all(state[0] == "terminal" for state in states.values()),
                         "completed launch scope contains unfinished attempts")
        return
    _require(isinstance(actor, str) and re.fullmatch(r"runner-agent-(0|[1-9][0-9]*)", actor),
             "actor must name a native configured invocation")
    previous, previous_key, attempt_time, is_tmux = states.get(
        actor, (None, None, None, False)
    )
    if kind == "launch_attempt":
        fields = {"agent_index"}
        if row["schema"] in (_V2, _V3, _V4):
            fields |= {"input_snapshot", "input_sha256"}
        if row["schema"] == _V4:
            fields.add("runner_context")
        if row["schema"] == _V4 and "tmux" in details:
            _require(previous is None and key is None and
                     set(details) == fields | {"tmux"},
                     "duplicate or invalid launch attempt")
            _tmux_launch(details["tmux"], details["agent_index"], row["timestamp_ns"])
            is_tmux = True
        else:
            _require(previous is None and key is None and set(details) == fields,
                     "duplicate or invalid launch attempt")
        _require(type(details["agent_index"]) is int and
                 details["agent_index"] == int(actor.removeprefix("runner-agent-")),
                 "agent index does not match invocation id")
        if "input_snapshot" in details:
            _launch_inputs(details)
        if "runner_context" in details:
            _runner_context(details["runner_context"], attempt_time=row["timestamp_ns"])
        states[actor] = ("attempted", None, row["timestamp_ns"], is_tmux)
    elif kind == "launch_failed":
        _require(previous == "attempted" and key is None and
                 set(details) == {"error_type"} and _text(details["error_type"]),
                 "launch failure is not bound to a pending attempt")
        states[actor] = ("terminal", None, attempt_time, is_tmux)
    elif kind == "agent_finished":
        _require(row["schema"] == _V4 and is_tmux and previous == "attempted" and
                 key is None and set(details) == {"status", "detail"},
                 "tmux completion is not bound to a tmux launch")
        _require(details["status"] in _TMUX_STATUSES and
                 isinstance(details["detail"], str),
                 "invalid tmux completion status")
        states[actor] = ("terminal", None, attempt_time, True)
    elif kind == "process_started":
        fields = {"observation"} if row["schema"] in (_V3, _V4) else set()
        _require(not is_tmux and previous == "attempted" and set(details) == fields,
                 "process start has no pending attempt")
        _process_key(key)
        if fields:
            _process_observation(details["observation"], key, lower=attempt_time, upper=row["timestamp_ns"])
        states[actor] = ("started", key, attempt_time, is_tmux)
    else:
        _require(not is_tmux and previous == "started" and key == previous_key and
                 set(details) == {"exit_code"} and type(details["exit_code"]) is int,
                 "process termination is not bound to the started process")
        states[actor] = ("terminal", key, attempt_time, is_tmux)


def _tree_digest(files):
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def _release_id_matches(reference_id, current_id, allow_historical_release):
    """Current id must match, unless historical binding is explicitly allowed."""
    return reference_id == current_id or allow_historical_release


def _verify_input_reference(rows, release_root, release, *, allow_historical_release=False):
    reference = rows[0]["details"].get("input_reference")
    if reference is None:
        return
    fields = {"schema", "release_id", "release_root", "scenario_id", "source_files",
              "source_fixture_dir", "materialized_fixture_dir", "pre_setup_fixture_sha256",
              "prepared_fixture_sha256", "setup"}
    _require(set(reference) == fields and reference["schema"] == "mtagentlens.execution-source/v1",
             "invalid execution source reference")
    _require(all(isinstance(reference[key], str) and re.fullmatch(r"[0-9a-f]{64}", reference[key])
                 for key in ("pre_setup_fixture_sha256", "prepared_fixture_sha256")),
             "invalid execution fixture digest")
    scenario_id = rows[0]["scenario_id"]
    _require(reference["scenario_id"] == scenario_id,
             "execution source reference does not match release/run")
    _require(_release_id_matches(reference["release_id"], release["release_id"], allow_historical_release),
             "execution source reference does not match release/run")
    scenario_path = "scenarios/" + scenario_id + ".json"
    _require(scenario_path in release["files"], "scenario input is not release-bound")
    scenario_bytes = (release_root / scenario_path).read_bytes()
    _require(hashlib.sha256(scenario_bytes).hexdigest() == release["files"][scenario_path],
             "scenario input changed while verifying")
    raw = _loads_strict(scenario_bytes.decode("utf-8"))
    _require(isinstance(raw, dict) and raw.get("id") == scenario_id, "source scenario identity mismatch")
    fixture_name = raw.get("fixture_dir", scenario_id)
    _require(isinstance(fixture_name, str), "invalid source fixture directory")
    relative_fixture = PurePosixPath("fixtures") / fixture_name
    _require(not PurePosixPath(fixture_name).is_absolute() and ".." not in relative_fixture.parts,
             "escaping source fixture directory")
    prefix = relative_fixture.as_posix() + "/"
    selected = {path: digest for path, digest in release["files"].items()
                if path == scenario_path or path.startswith(prefix) or path.startswith("bench/")}
    _require(reference["source_files"] == selected, "execution source inventory does not match release")
    binding = release["case_bindings"].get(scenario_id, {})
    source_files = {path[len(prefix):]: digest for path, digest in selected.items() if path.startswith(prefix)}
    _require(binding.get("scenario_sha256") == selected[scenario_path] and
             binding.get("fixture_sha256") == _tree_digest(source_files), "case input hashes do not match release")
    for name in ("release_root", "source_fixture_dir", "materialized_fixture_dir"):
        _require(_text(reference[name]), "invalid execution source path")
        path = PurePosixPath(reference[name])
        _require(path.is_absolute() and ".." not in path.parts and path.as_posix() == reference[name],
                 "execution source paths must be canonical and absolute")
    historical_root = PurePosixPath(reference["release_root"])
    _require(reference["source_fixture_dir"] == (historical_root / relative_fixture).as_posix(),
             "source fixture path does not match its release location")
    destination = reference["materialized_fixture_dir"]
    _require(PurePosixPath(destination).name == "fixtures", "invalid materialized fixture location")
    _require(not PurePosixPath(destination).is_relative_to(historical_root),
             "materialized fixture cannot be inside the immutable release")
    expected = {}
    for relative, digest in source_files.items():
        data = (release_root / relative_fixture / relative).read_bytes()
        _require(hashlib.sha256(data).hexdigest() == digest, "source input changed while verifying")
        if len(data) <= 2 * 1024 * 1024:
            try:
                text = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
                rewritten = text.replace("{{FIXTURE_DIR}}", destination).replace(reference["source_fixture_dir"], destination)
                if rewritten != text:
                    data = rewritten.encode("utf-8")
            except UnicodeDecodeError:
                pass
        expected[relative] = hashlib.sha256(data).hexdigest()
    _require(reference["pre_setup_fixture_sha256"] == _tree_digest(expected),
             "materialized fixture does not match declared source transformation")
    script = raw.get("pre_run_script")
    if script:
        _require(isinstance(script, str), "invalid source setup path")
        _require(isinstance(reference["setup"], dict) and
                 type(reference["setup"].get("exit_code")) is int,
                 "invalid setup execution status")
        parts = PurePosixPath(script).parts
        script = PurePosixPath(*parts[2:]).as_posix() if parts[:3] == ("env", "scenarios", "bench") else script
        script = posixpath.normpath(script)
        _require(script in selected and script.startswith("bench/") and reference["setup"] ==
                 {"path": script, "sha256": selected[script], "exit_code": 0}, "unbound setup execution")
    else:
        _require(reference["setup"] is None and reference["prepared_fixture_sha256"] == _tree_digest(expected),
                 "fixture changed without a declared setup")
    agents = raw.get("agents")
    _require(isinstance(agents, list) and agents and all(isinstance(agent, dict) and
             isinstance(agent.get("task"), str) for agent in agents),
             "source scenario has no valid configured invocations")
    attempts = [row for row in rows if row["event_type"] == "launch_attempt"]
    _require([row["details"]["agent_index"] for row in attempts] == list(range(len(attempts))) and
             len(attempts) <= len(agents), "launch order does not match configured invocations")
    if rows[-1]["details"]["state"] == "completed":
        _require(len(attempts) == len(agents), "completed source scope omits configured invocations")
    for index, attempt in enumerate(attempts):
        snapshot = attempt["details"]["input_snapshot"]
        prompt = agents[index]["task"].replace("{{FIXTURE_DIR}}", destination).replace(reference["source_fixture_dir"], destination)
        _require(snapshot["prompt"] == prompt and snapshot["cwd"] == str(PurePosixPath(destination).parent),
                 "launch prompt or working directory does not match source transformation")
        args = snapshot["claude_args"][2:]
        for flag, value in (("--model", raw.get("model", "haiku")), ("--effort", str(raw.get("effort", "low")))):
            _require(args.count(flag) == 1 and args.index(flag) + 1 < len(args) and args[args.index(flag) + 1] == value,
                     "launch client options do not match source scenario")
        permission = raw.get("permission_mode", "default")
        _require((permission == "default" and "--permission-mode" not in args) or
                 (permission != "default" and args.count("--permission-mode") == 1 and
                  args.index("--permission-mode") + 1 < len(args) and args[args.index("--permission-mode") + 1] == permission),
                 "launch permission mode does not match source scenario")
        if index == 0:
            tree = snapshot["fixtures"]
            _require(tree is not None and reference["prepared_fixture_sha256"] ==
                     _tree_digest({name: entry["sha256"] for name, entry in tree["files"].items()}),
                     "prepared fixture differs from first launch inputs")


def _unbound_source_manifest(rows: list, release_root: Path) -> dict:
    """Compare a run's recorded source inputs with the current ordinary dataset.

    This creates no release identity: the data only describes the source files
    captured by the run, so it cannot authenticate a historical dataset release.
    """
    from mtagentlens_benchmark.engine.input_reference import (
        _file_shas, _read_regular, _reject_symlink_path, _snapshot_tree, _within,
    )

    reference = rows[0]["details"].get("input_reference")
    _require(isinstance(reference, dict) and reference.get("release_id") is None,
             "unbound run requires an unbound input reference")
    _require(not release_root.is_symlink() and release_root.is_dir(), "invalid dataset directory")
    root = release_root.resolve()
    sid = rows[0]["scenario_id"]
    _require(_text(sid) and sid not in (".", "..") and "/" not in sid and
             "\\" not in sid and "\x00" not in sid, "invalid source scenario identity")
    scenario_path = root / "scenarios" / (sid + ".json")
    _reject_symlink_path(scenario_path, root)
    raw_bytes = _read_regular(scenario_path, "scenario JSON")
    raw = _loads_strict(raw_bytes.decode("utf-8"))
    _require(isinstance(raw, dict) and raw.get("id") == sid, "source scenario identity mismatch")
    fixture = raw.get("fixture_dir") or sid
    _require(isinstance(fixture, str) and fixture and not PurePosixPath(fixture).is_absolute()
             and ".." not in PurePosixPath(fixture).parts, "invalid source fixture directory")
    fixture_root = root / "fixtures"
    fixture_dir = fixture_root / fixture
    _within(fixture_dir, fixture_root, "scenario fixture directory")
    _reject_symlink_path(fixture_dir, root)
    fixture_files = _file_shas(_snapshot_tree(fixture_dir, "scenario fixture directory"))
    # Older runner versions recorded every bench file. Keep those recorded
    # declarations but verify only the setup script this scenario actually used.
    # An unrelated bench edit must not invalidate an otherwise readable run.
    recorded = reference.get("source_files")
    _require(isinstance(recorded, dict), "invalid recorded source inventory")
    bench_files = {}
    for name, digest in recorded.items():
        if isinstance(name, str) and name.startswith("bench/"):
            relative = PurePosixPath(name)
            _require(relative.as_posix() == name and ".." not in relative.parts and
                     isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
                     "invalid recorded bench source")
            bench_files[name] = digest
    setup = reference.get("setup")
    if setup is not None:
        _require(isinstance(setup, dict) and _text(setup.get("path")), "invalid setup source")
        setup_path = setup["path"]
        _require(setup_path in bench_files, "setup source is absent from runner record")
        target = root / setup_path
        _reject_symlink_path(target, root)
        actual = hashlib.sha256(_read_regular(target, "setup script")).hexdigest()
        _require(actual == bench_files[setup_path], "setup source changed while verifying")
    source_path = f"scenarios/{sid}.json"
    scenario_sha = hashlib.sha256(raw_bytes).hexdigest()
    files = {source_path: scenario_sha}
    fixture_prefix = (PurePosixPath("fixtures") / fixture).as_posix()
    files.update({f"{fixture_prefix}/{name}": digest for name, digest in fixture_files.items()})
    files.update(bench_files)
    return {"release_id": None, "files": files, "case_bindings": {
        sid: {"scenario_sha256": scenario_sha, "fixture_sha256": _tree_digest(fixture_files)}}}


def read_runner_evidence(ledger: Path, *, release_root: Path,
                         allow_historical_release: bool = False) -> dict:
    """Verify native records and any execution-source reference without writing."""
    ledger = Path(ledger)
    _require(not ledger.is_symlink() and ledger.is_file(), "ledger must be a regular file")
    raw = ledger.read_bytes()
    _require(raw and raw.endswith(b"\n"), "ledger is empty or truncated")
    rows, events, states, ids = [], [], {}, set()
    offset, previous_time = 0, -1
    common_keys = ("schema", "producer", "run_id", "scenario_id", "source_run_uuid", "clock_domain")
    for line in raw.splitlines(keepends=True):
        row = _loads_strict(line.decode("utf-8"))
        _require(isinstance(row, dict) and set(row) == _KEYS, "invalid runner event fields")
        _require(row["schema"] in (_V1, _V2, _V3, _V4),
                 "unsupported runner event schema")
        _require(all(_text(row[key]) for key in common_keys), "invalid runner binding")
        version = row["schema"].rsplit("/v", 1)[1]
        _require(re.fullmatch(r"process-audit/" + version + r"@sha256:[0-9a-f]{64}", row["producer"]),
                 "unsupported runner producer")
        _uuid(row["source_run_uuid"])
        _uuid(row["event_id"], hex_only=True)
        _require(row["event_id"] not in ids, "duplicate native event id")
        ids.add(row["event_id"])
        _require(row["clock_domain"] == "runner-monotonic-ns" and
                 type(row["timestamp_ns"]) is int and row["timestamp_ns"] >= 0
                 and row["timestamp_ns"] >= previous_time,
                 "invalid or reversed runner clock")
        previous_time = row["timestamp_ns"]
        _require(isinstance(row["event_type"], str) and row["event_type"] in _TYPES and
                 isinstance(row["details"], dict), "unsupported native event")
        if rows:
            _require(all(row[key] == rows[0][key] for key in common_keys), "mixed runner binding")
            _require(rows[-1]["event_type"] != "run_finished" and row["event_type"] != "run_started",
                     "records outside a single launch scope")
        else:
            _require(row["event_type"] == "run_started", "ledger must begin at run_started")
        _transition(row, states)
        events.append({"event_id": row["event_id"], "source_id": "runner",
                       "record_id": row["event_id"], "timestamp": str(row["timestamp_ns"]),
                       "clock_domain": row["clock_domain"],
                       "derivation": "native top-level process lifecycle; no internal actor assignment",
                       "byte_offset": offset, "byte_length": len(line),
                       "record_sha256": hashlib.sha256(line).hexdigest()})
        rows.append(row)
        offset += len(line)
    _require(rows[-1]["event_type"] == "run_finished", "ledger has no frozen scope end")
    release_root = Path(release_root)
    manifest_path = release_root / "release.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        release = validate_release(release_root)
        _require(isinstance(release, dict) and _text(release.get("release_id")),
                 "release identity is missing")
    else:
        release = _unbound_source_manifest(rows, release_root)
    _verify_input_reference(rows, release_root, release,
                            allow_historical_release=allow_historical_release)
    first = rows[0]
    binding = {key: first[key] for key in ("run_id", "scenario_id", "source_run_uuid")}
    reference = first["details"].get("input_reference") or {}
    release_id_at_run = reference.get("release_id")
    if release_id_at_run == release["release_id"]:
        release_id_at_run = None
    binding.update(data_release_id=release["release_id"])
    return {"raw": raw, "rows": rows, "events": events, "actors": sorted(states),
            "binding": binding, "release_id_at_run": release_id_at_run}


def _discover_collector_db(ledger: Path) -> Path | None:
    candidate = ledger.parent / "collector" / "finalized.db"
    return candidate if candidate.is_file() else None


def import_runner_bundle(output: Path, *, release_root: Path, ledger: Path,
                         codebook_version: str, collector_db: Path | None = None) -> dict:
    ledger = Path(ledger)
    evidence = read_runner_evidence(ledger, release_root=release_root)
    binding = dict(evidence["binding"], codebook_version=codebook_version)
    first = evidence["rows"][0]
    events = list(evidence["events"])
    actors = set(evidence["actors"])
    sources = {"runner": {"path": ledger, "kind": "controlled_runner",
                          "producer": first["producer"]}}
    database = Path(collector_db) if collector_db is not None else _discover_collector_db(ledger)
    temporary = None
    try:
        if database is not None:
            from tools.annotator.collector_evidence import (
                PRODUCER, SOURCE_KIND, export_collector_evidence)
            temporary = Path(tempfile.mkdtemp(prefix="mtl-collector-evidence-"))
            export_path, database_events, database_actors = export_collector_evidence(
                database, temporary / "collector-evidence.jsonl", source_id="collector")
            events.extend(database_events)
            actors.update(database_actors)
            sources["collector"] = {"path": export_path, "kind": SOURCE_KIND,
                                    "producer": PRODUCER}
        from tools.annotator.blinding import binding_identifiers
        from tools.annotator.task_brief import (
            PRODUCER as BRIEF_PRODUCER, SOURCE_ID as BRIEF_SOURCE,
            SOURCE_KIND as BRIEF_KIND, extract_task_brief, write_task_brief)
        from tools.annotator.trajectory import (
            PRODUCER as TRAJECTORY_PRODUCER, SOURCE_ID as TRAJECTORY_SOURCE,
            SOURCE_KIND as TRAJECTORY_KIND, extract_trajectory, write_trajectory)
        trace_log = ledger.parent / "collector" / "collector.log"
        trajectory = extract_trajectory(trace_log, identifiers=binding_identifiers(binding))
        if trajectory is not None:
            if temporary is None:
                temporary = Path(tempfile.mkdtemp(prefix="mtl-evidence-"))
            trajectory_path, trajectory_events = write_trajectory(
                temporary / "trajectory.jsonl", trajectory[1])
            events.extend(trajectory_events)
            sources[TRAJECTORY_SOURCE] = {"path": trajectory_path,
                                          "kind": TRAJECTORY_KIND,
                                          "producer": TRAJECTORY_PRODUCER}
        brief = extract_task_brief(Path(release_root), binding["scenario_id"],
                                   identifiers=binding_identifiers(binding))
        if brief is not None:
            if temporary is None:
                temporary = Path(tempfile.mkdtemp(prefix="mtl-evidence-"))
            brief_path, brief_events = write_task_brief(temporary / "task-brief.jsonl", brief[1])
            events.extend(brief_events)
            sources[BRIEF_SOURCE] = {"path": brief_path, "kind": BRIEF_KIND,
                                     "producer": BRIEF_PRODUCER}
        result = build_bundle(Path(output), release_root=Path(release_root), binding=binding,
                              sources=sources, actors=sorted(actors), events=events)
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
    if result["sources"]["runner"]["sha256"] != hashlib.sha256(evidence["raw"]).hexdigest():
        shutil.rmtree(output)
        raise ValueError("runner source changed while freezing evidence")
    return result
