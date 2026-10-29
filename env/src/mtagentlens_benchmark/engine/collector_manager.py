"""MtAgentLens collector lifecycle — single-process eBPF collector + tcpdump.

Phase 1 hardened: uprobe verification, TLS canary test, runtime health monitoring,
comprehensive binary detection with diagnostics.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import time
from pathlib import Path

from mtagentlens_benchmark.types import CollectorInterface
from mtagentlens_benchmark.workspace import BenchmarkWorkspace


# ═══════════════════════════════════════════════════════════════════════════════
#  Binary detection — comprehensive, diagnostic, public for executor reuse
# ═══════════════════════════════════════════════════════════════════════════════

def _is_elf_binary(path: Path) -> bool:
    """Check if a file is an ELF binary by reading its magic bytes."""
    try:
        with open(path, "rb") as f:
            return f.read(4) == b"\x7fELF"
    except (OSError, PermissionError):
        return False


def _resolve_elf_from_wrapper(wrapper_path: Path) -> list[str]:
    """If the claude path is a shell wrapper script, extract possible real binary paths.

    Returns a list of candidate ELF paths found in the wrapper, ordered by likelihood.
    """
    candidates: list[str] = []
    try:
        with open(wrapper_path, "r") as f:
            content = f.read(4096)

        # Match: exec /path/to/real/claude "$@"
        for m in re.finditer(r'exec\s+(/\S+/claude(?:\.exe)?)\s', content):
            real = Path(m.group(1))
            if real.exists() and _is_elf_binary(real):
                candidates.append(str(real))

        # Match: node /path/to/cli.js or bun /path/to/server.js
        for m in re.finditer(r'(?:node|bun)\s+(/\S+\.(?:js|mjs))\s', content):
            candidates.append(m.group(1))

    except (OSError, PermissionError):
        pass
    return candidates


def _search_claude_install_paths(user_home: str) -> list[str]:
    """Search all known Claude Code installation paths for the ELF binary.

    Returns a list of found binary paths, ordered by version (newest first).
    """
    found: list[str] = []

    # npm global install
    npm_lib = Path(user_home) / ".local" / "lib" / "claude-code"
    if npm_lib.is_dir():
        for ver_dir in sorted(npm_lib.iterdir(), reverse=True):
            for name in ("claude", "claude.exe"):
                p = ver_dir / name
                if p.exists() and _is_elf_binary(p):
                    found.append(str(p))

    # nvm installs — multiple patterns
    nvm_versions = Path(user_home) / ".nvm" / "versions" / "node"
    if nvm_versions.is_dir():
        for v in sorted(nvm_versions.iterdir(), reverse=True):
            base = v / "lib" / "node_modules" / "@anthropic-ai" / "claude-code"
            for candidate_name in (
                "bin/claude.exe",
                "node_modules/@anthropic-ai/claude-code-linux-x64/claude",
                "bin/claude",
            ):
                p = base / candidate_name
                if p.exists() and _is_elf_binary(p):
                    found.append(str(p))

    # Legacy npm prefix paths
    for prefix in ("/usr/local", "/usr"):
        for sub in (
            "lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe",
            "bin/claude.exe",
            "lib/node_modules/@anthropic-ai/claude-code/bin/claude",
        ):
            p = Path(prefix) / sub
            if p.exists() and _is_elf_binary(p):
                found.append(str(p))

    return found


def _find_claude_binary_with_diagnostics() -> tuple[str | None, dict]:
    """Auto-detect the Claude Code ELF binary for tls_capture uprobe.

    tls_capture needs the actual Bun-compiled ELF binary (with BoringSSL
    statically linked) for byte-pattern matching. Shell wrappers and
    symlinks to wrappers won't work.

    Returns (binary_path_or_none, diagnostics_dict).
    """
    diagnostics: dict = {
        "searched_paths": [],
        "candidates_checked": [],
        "rejected_reasons": [],
        "final_path": None,
    }

    override = os.environ.get("MTAGENTLENS_AGENT_BINARY", "").strip()
    if override:
        diagnostics["searched_paths"].append(f"MTAGENTLENS_AGENT_BINARY -> {override}")
        candidate = Path(override)
        if candidate.exists() and _is_elf_binary(candidate):
            diagnostics["final_path"] = str(candidate)
            return str(candidate), diagnostics
        diagnostics["rejected_reasons"].append(
            f"MTAGENTLENS_AGENT_BINARY is not an ELF binary: {override}"
        )

    claude_path = shutil.which("claude")
    if claude_path:
        resolved = Path(claude_path).resolve()
        diagnostics["searched_paths"].append(f"which claude -> {resolved}")
        if resolved.exists():
            if _is_elf_binary(resolved):
                diagnostics["final_path"] = str(resolved)
                return str(resolved), diagnostics
            # Shell wrapper — try to extract real binary
            wrapper_candidates = _resolve_elf_from_wrapper(resolved)
            diagnostics["candidates_checked"].extend(wrapper_candidates)
            for candidate in wrapper_candidates:
                if _is_elf_binary(Path(candidate)):
                    diagnostics["final_path"] = candidate
                    return candidate, diagnostics
            diagnostics["rejected_reasons"].append(
                f"which claude resolved to non-ELF: {resolved}"
            )
        else:
            diagnostics["rejected_reasons"].append(
                f"which claude path does not exist: {resolved}"
            )
    else:
        diagnostics["rejected_reasons"].append("claude not found in PATH")

    original_user = os.environ.get("SUDO_USER", "")
    user_home = (
        os.path.expanduser(f"~{original_user}") if original_user
        else os.path.expanduser("~")
    )
    diagnostics["user_home"] = user_home

    install_candidates = _search_claude_install_paths(user_home)
    diagnostics["candidates_checked"].extend(install_candidates)
    for candidate in install_candidates:
        if _is_elf_binary(Path(candidate)):
            diagnostics["final_path"] = candidate
            return candidate, diagnostics

    if not install_candidates:
        diagnostics["rejected_reasons"].append(
            f"no claude installation found under {user_home}"
        )

    return None, diagnostics


def _find_claude_binary() -> str | None:
    """Backward-compatible wrapper that returns path or None."""
    path, _diag = _find_claude_binary_with_diagnostics()
    return path


# ═══════════════════════════════════════════════════════════════════════════════
#  Uprobe verification
# ═══════════════════════════════════════════════════════════════════════════════

UPROBE_EVENTS_PATH = "/sys/kernel/debug/tracing/uprobe_events"


def _verify_uprobes_attached(binary_path: str, timeout: float = 5.0) -> dict:
    """Verify eBPF uprobes are attached to the target binary.

    Checks /sys/kernel/debug/tracing/uprobe_events for entries matching
    the given binary path. Also checks if debugfs is mounted.

    Returns a diagnostic dict with keys: attached, uprobe_count,
    matching_uprobes, errors.
    """
    diag: dict = {
        "binary_path": binary_path,
        "debugfs_accessible": False,
        "uprobe_events_accessible": False,
        "attached": False,
        "uprobe_count": 0,
        "matching_uprobes": [],
        "errors": [],
    }

    # Check uprobe_events accessibility (may require sudo)
    uprobe_path = Path(UPROBE_EVENTS_PATH)
    try:
        _ = uprobe_path.exists()
        diag["debugfs_accessible"] = True
    except PermissionError:
        # Non-root user; rely on sudo fallback below
        diag["debugfs_accessible"] = True  # Assume accessible via sudo

    try:
        deadline = time.time() + timeout
        result = None
        while time.time() < deadline:
            try:
                result = subprocess.run(
                    ["cat", UPROBE_EVENTS_PATH],
                    capture_output=True, text=True, timeout=3,
                )
                if result.returncode == 0:
                    diag["uprobe_events_accessible"] = True
                    break
            except subprocess.TimeoutExpired:
                pass
            time.sleep(0.3)

        if not diag["uprobe_events_accessible"]:
            try:
                sudo_pass = os.environ.get("MTAGENTLENS_SUDO_PASSWORD", "")
                if sudo_pass:
                    result = subprocess.run(
                        ["sudo", "-S", "cat", UPROBE_EVENTS_PATH],
                        input=sudo_pass + "\n", capture_output=True, text=True, timeout=5,
                    )
                else:
                    result = subprocess.run(
                        ["sudo", "-n", "cat", UPROBE_EVENTS_PATH],
                        capture_output=True, text=True, timeout=3,
                    )
                if result.returncode == 0:
                    diag["uprobe_events_accessible"] = True
                else:
                    diag["errors"].append(
                        f"Cannot read {UPROBE_EVENTS_PATH} "
                        f"(exit {result.returncode}): {result.stderr.strip()[:200]}"
                    )
                    return diag
            except (subprocess.TimeoutExpired, FileNotFoundError):
                diag["errors"].append(f"Cannot access {UPROBE_EVENTS_PATH}")
                return diag

        if result is None or not result.stdout.strip():
            diag["uprobe_count"] = 0
            # tls_capture attaches with bpf_program__attach_uprobe
            # (perf_event_open), which does not create tracefs entries.
            # An empty file is therefore not evidence of failure; the runtime
            # TLS-session count is the authoritative check.
            diag["note"] = (
                "uprobe_events is empty; perf_event-based uprobes do not register "
                "tracefs entries. Runtime TLS sessions decide capture health."
            )
            return diag

        lines = [l.strip() for l in result.stdout.strip().split("\n") if l.strip()]
        diag["uprobe_count"] = len(lines)

        binary_name = Path(binary_path).name
        for line in lines:
            if binary_path in line or binary_name in line:
                diag["matching_uprobes"].append(line)

        diag["attached"] = len(diag["matching_uprobes"]) > 0
        if not diag["attached"]:
            diag["errors"].append(
                f"No uprobes found for {binary_path} among {len(lines)} entries. "
                "tls_capture may not have attached. Check collector startup logs."
            )

    except Exception as e:
        diag["errors"].append(f"Exception reading uprobe_events: {e}")

    return diag


# ═══════════════════════════════════════════════════════════════════════════════
#  TLS canary test
# ═══════════════════════════════════════════════════════════════════════════════

CANARY_URL = "https://httpbin.org/get?mtagentlens-canary=verify"


def _canary_captured(output_dir: Path, log_file: str = "collector") -> bool:
    """Find the marker in a captured TLS/HTTP event, not a curl argv/log line."""
    for path in (output_dir / log_file, output_dir / f"{log_file}.log"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            start = line.find("{")
            if start < 0:
                continue
            try:
                event, _ = json.JSONDecoder().raw_decode(line[start:])
            except ValueError:
                continue
            if isinstance(event, dict) and event.get("source") in {"ssl", "http"}:
                if "mtagentlens-canary" in json.dumps(event.get("data"), ensure_ascii=False):
                    return True
    return False


def _tls_canary_test(output_dir: Path, timeout: float = 10.0,
                     log_file: str = "collector",
                     comm_filter: str | None = None) -> dict:
    """Send a known TLS request and verify the collector captured it.

    Uses curl to make a marked HTTPS request. After a short wait, checks
    the collector's output for evidence of TLS capture.

    A comm filter that does not include ``curl`` makes the canary
    inapplicable: the request is deliberately outside the capture boundary,
    so its absence is expected and must not be reported as a failure.
    """
    diag: dict = {
        "canary_url": CANARY_URL,
        "sent": False,
        "curl_ok": False,
        "captured_in_log": False,
        "errors": [],
    }
    if comm_filter and comm_filter not in ("", "curl"):
        diag["skipped"] = True
        diag["skipped_reason"] = (
            f"collector comm filter '{comm_filter}' excludes the curl canary; "
            "capture health is decided by runtime TLS sessions"
        )
        return diag

    try:
        result = subprocess.run(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", CANARY_URL],
            capture_output=True, text=True, timeout=min(timeout / 2, 5),
        )
        diag["sent"] = True
        diag["curl_ok"] = result.returncode == 0 and result.stdout.strip() == "200"
        diag["curl_http_code"] = result.stdout.strip()
    except Exception as e:
        diag["errors"].append(f"Canary curl failed: {e}")
        return diag

    # Wait for the collector to process and log the event, then actually
    # LOOK for it. This used to sleep and return with `captured_in_log` still
    # at its initialised False — a check that could never fire, while the
    # docstring claimed it verified capture.
    time.sleep(2.0)

    diag["captured_in_log"] = _canary_captured(output_dir, log_file)
    if not diag["captured_in_log"]:
        diag["errors"].append(
            "canary request not found in collector output; TLS capture may be inactive"
        )

    return diag


# ═══════════════════════════════════════════════════════════════════════════════
#  Runtime metrics collection (post-stop)
# ═══════════════════════════════════════════════════════════════════════════════

def _run_context_args(scenario_id: str | None, mode: str | None) -> list[str]:
    """CLI args that bind the collector's runs row to a benchmark scenario."""
    args: list[str] = []
    if scenario_id:
        args.extend(["--scenario-id", scenario_id])
    if mode:
        args.extend(["--run-mode", mode])
    return args


CAPTURE_SOURCE_NAMES = frozenset({"tls", "process", "stdio"})


def _configured_capture_sources(trace_args: list[str]) -> set[str]:
    """Which capture sources the run asked the collector to start.

    Loss counters can only prove completeness for sources that were supposed
    to report. Deriving the expectation from the command line (rather than
    from the log) keeps a crashed or silent helper visible as a gap.
    """
    # Trace's clap defaults enable SSL and process, including when omitted.
    expected: set[str] = {"tls", "process"}
    names = {"--ssl": "tls", "--process": "process", "--stdio": "stdio"}
    index = 0
    while index < len(trace_args):
        arg = trace_args[index]
        index += 1
        if arg == "--":
            break
        name, separator, value = arg.partition("=")
        if name not in names:
            continue
        if not separator:
            value = "true"
            if index < len(trace_args) and trace_args[index].lower() in {"true", "false"}:
                value = trace_args[index]
                index += 1
        if value.lower() not in {"true", "false"}:
            raise ValueError(f"invalid capture toggle: {arg}")
        if value.lower() == "true":
            expected.add(names[name])
        else:
            expected.discard(names[name])
    return expected


def _validate_capture_sources(value) -> set[str]:
    if not isinstance(value, (list, tuple, set)):
        raise ValueError("configured capture sources must be a list of names")
    sources: set[str] = set()
    for item in value:
        if not isinstance(item, str) or item not in CAPTURE_SOURCE_NAMES:
            raise ValueError(f"unknown capture source: {item!r}")
        sources.add(item)
    return sources


def _recover_configured_capture_sources(health: dict) -> set[str] | None:
    """Recover a run's expected capture sources from its health snapshot.

    Returns ``None`` when the snapshot carries no configuration at all. A
    present but malformed configuration raises ``ValueError`` so callers can
    fail closed instead of treating logs alone as completeness evidence.

    Logs are only written by sources that ran; they cannot prove that a
    configured source was absent. The persisted config is the authority.
    """
    if not isinstance(health, dict):
        raise ValueError("collector health snapshot must be an object")
    runtime = health.get("runtime_metrics")
    if not isinstance(runtime, dict):
        runtime = {}
    candidates: list[set[str]] = []
    for container in (health, runtime):
        if "configured_capture_sources" in container:
            candidates.append(_validate_capture_sources(container["configured_capture_sources"]))
        if "trace_args" in container:
            trace_args = container["trace_args"]
            if not isinstance(trace_args, list) or not all(
                isinstance(arg, str) for arg in trace_args
            ):
                raise ValueError("trace_args must be a list of strings")
            candidates.append(_configured_capture_sources(trace_args))
    if not candidates:
        return None
    if any(candidate != candidates[0] for candidate in candidates[1:]):
        raise ValueError("conflicting capture source configurations")
    return candidates[0]


def _parse_runtime_metrics_from_logs(
    err_log_text: str,
    trace_out_path: Path,
    configured_sources: set[str] | None = None,
) -> dict:
    """Parse collector log output for runtime health metrics.

    Extracts: SSL/HTTP event counts, ring buffer drops, PBC edge counts,
    scoring signal counts, cluster counts, filter availability, and
    persistence writer stats.
    """
    metrics: dict = {
        "has_trace_output": trace_out_path.exists() and trace_out_path.stat().st_size > 0,
        "trace_output_bytes": trace_out_path.stat().st_size if trace_out_path.exists() else 0,
    }

    # Event logs are written to the trace file; stderr holds collector
    # diagnostics. The old regexes (`"event":"SSL"`) matched neither current
    # event shape, so every capture-health run reported zero SSL/HTTP events.
    trace_text = ""
    try:
        if trace_out_path.exists():
            trace_text = trace_out_path.read_text(errors="replace")
    except OSError:
        pass
    event_text = err_log_text + "\n" + trace_text

    ssl_pattern = re.compile(r'"source"\s*:\s*"ssl"|"event"\s*:\s*"SSL"')
    metrics["ssl_events"] = len(ssl_pattern.findall(event_text))

    # The HTTPParser analyzer emits `source: "http_parser"`, not `"http"`.
    # Counting only the legacy literal made every run report http_events=0 and
    # let a run whose request reconstruction failed still pass health.
    http_pattern = re.compile(r'"source"\s*:\s*"http(?:_parser)?"|"event"\s*:\s*"HTTP')
    metrics["http_events"] = len(http_pattern.findall(event_text))

    # Each source reports cumulative counters. Periodic TLS rows and its final
    # TLS_RINGBUF_STATS are the same counter, not independent lost events.
    sources = {}
    final_sources = set()
    # Counters are losses: a missing or malformed value is unknown, never zero.
    # Booleans are rejected explicitly because bool is an int subclass.
    optional_counter_keys = (
        "rate_limit_drops",
        "aggregation_table_full",
        "ringbuf_full_dropped",
        "pending_table_dropped",
        "user_read_dropped",
    )

    def _is_valid_counter(value) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0

    def _valid_stats_row(row: dict) -> bool:
        # dropped_events is the required total for every stats row. Legacy
        # helpers may omit the per-cause split; if present it must be valid.
        if not _is_valid_counter(row.get("dropped_events")):
            return False
        return all(
            _is_valid_counter(row[key]) for key in optional_counter_keys if key in row
        )

    # Seed with the configured sources: a helper that never prints its final
    # stats must leave the run incomplete, not quietly drop out of the check.
    expected_sources = set(configured_sources or ())
    metrics["stdio_events"] = 0
    for line in event_text.splitlines():
        start = line.find('{')
        if start < 0:
            continue
        try:
            row, _ = json.JSONDecoder().raw_decode(line[start:])
        except ValueError:
            continue
        if row.get("source") == "stdio":
            expected_sources.add("stdio")
            if isinstance(row.get("data"), dict):
                row = row["data"]
        if (row.get("direction") in {"READ", "WRITE"}
                and isinstance(row.get("data"), str) and row["data"]
                and not row.get("copy_failed")):
            metrics["stdio_events"] += 1
        if row.get("direction") == "STATS":
            expected_sources.add("stdio")
            if _valid_stats_row(row):
                sources.setdefault("stdio", {}).update(row)
                final_sources.add("stdio")
            else:
                sources.pop("stdio", None)
                final_sources.discard("stdio")
            continue
        event = row.get("event")
        if event in {"TLS_CAPTURE_PROBES", "TLS_CAPTURE_ERROR"}:
            expected_sources.add("tls")
        source = {"RINGBUF_STATS": "tls", "TLS_RINGBUF_STATS": "tls",
                  "PROCESS_RINGBUF_STATS": "process"}.get(event)
        if source is None:
            continue
        expected_sources.add(source)
        if not _valid_stats_row(row):
            # A later corrupt counter invalidates prior cumulative evidence.
            sources.pop(source, None)
            final_sources.discard(source)
            continue
        sources.setdefault(source, {}).update(row)
        if event != "RINGBUF_STATS":
            final_sources.add(source)
    metrics["ring_buffer_drops"] = (
        sum(row.get("dropped_events", 0) for row in sources.values()) if sources else None
    )
    metrics["ring_buffer_drop_events"] = len(sources)
    metrics["rate_limit_drops"] = sum(row.get("rate_limit_drops", 0) for row in sources.values())
    metrics["aggregation_table_full"] = sum(row.get("aggregation_table_full", 0) for row in sources.values())
    metrics["capture_stats_complete"] = bool(sources) and final_sources == expected_sources
    metrics["capture_loss_free"] = bool(
        metrics["capture_stats_complete"]
        and metrics["ring_buffer_drops"] == 0
        and metrics["rate_limit_drops"] == 0
    )
    metrics["capture_sources"] = sources
    # Per-cause loss split. Older helpers only emit the total; reporting zero
    # for a cause they never measured would fabricate attribution.
    reason_keys = ("ringbuf_full_dropped", "pending_table_dropped", "user_read_dropped")
    if any(any(name in row for name in reason_keys) for row in sources.values()):
        def _reason(name: str) -> int:
            return sum(int(row.get(name, 0) or 0) for row in sources.values())

        metrics["capture_loss_reasons"] = {
            "ringbuf_full": _reason("ringbuf_full_dropped"),
            "pending_table": _reason("pending_table_dropped"),
            "user_read": _reason("user_read_dropped"),
        }
    else:
        metrics["capture_loss_reasons"] = None

    # Structured diagnostics line: the authoritative per-run counters.
    diagnostics = {}
    for line in err_log_text.splitlines():
        if "[diagnostics:json]" not in line:
            continue
        try:
            diagnostics = json.loads(line.split("[diagnostics:json]", 1)[1].strip())
        except ValueError:
            continue
    if diagnostics:
        metrics["diagnostics"] = diagnostics
        metrics["actions_produced"] = diagnostics.get("actions_produced")
        metrics["evidence_intents"] = diagnostics.get("evidence_intents")
        metrics["taint_edges"] = diagnostics.get("taint_edges")

    # PBC edge counts from scoring output
    pbc_pattern = re.compile(r'pbc_in_graph=(\d+)')
    pbc_counts = [int(m) for m in pbc_pattern.findall(err_log_text)]
    metrics["max_pbc_in_graph"] = max(pbc_counts) if pbc_counts else 0
    metrics["min_pbc_in_graph"] = min(pbc_counts) if pbc_counts else 0

    # Scoring output: has_signal counts
    signal_pattern = re.compile(r'has_signal=(\d+)')
    signal_counts = [int(m) for m in signal_pattern.findall(err_log_text)]
    metrics["max_has_signal"] = max(signal_counts) if signal_counts else 0

    # Total edges
    edge_pattern = re.compile(r'total_edges=(\d+)')
    edge_counts = [int(m) for m in edge_pattern.findall(err_log_text)]
    metrics["max_total_edges"] = max(edge_counts) if edge_counts else 0

    # Cluster/claim counts
    cluster_pattern = re.compile(r'clusters=(\d+)')
    cluster_counts = [int(m) for m in cluster_pattern.findall(err_log_text)]
    metrics["max_clusters"] = max(cluster_counts) if cluster_counts else 0

    # HTTPFilter/SSLFilter metrics availability
    has_text = lambda needle: needle in err_log_text
    metrics["http_filter_has_metrics"] = (
        has_text("HTTPFilter Global Metrics")
        and "No metrics available" not in err_log_text
    )
    metrics["ssl_filter_has_metrics"] = (
        has_text("SSLFilter Global Metrics")
        and "No metrics available" not in err_log_text
    )

    # Persistence writer stats (final line)
    persist_pattern = re.compile(
        r'Persistence: snapshots w=(\d+) s=(\d+) \| actions w=(\d+) f=(\d+)'
    )
    persist_matches = list(persist_pattern.finditer(err_log_text))
    if persist_matches:
        last = persist_matches[-1]
        metrics["persistence_snapshots_written"] = int(last.group(1))
        metrics["persistence_snapshots_skipped"] = int(last.group(2))
        metrics["persistence_actions_written"] = int(last.group(3))
        metrics["persistence_actions_failed"] = int(last.group(4))

    # Derived health booleans
    metrics["ssl_uprobe_healthy"] = bool(
        metrics.get("ssl_events", 0) > 0 or metrics.get("ssl_filter_has_metrics")
    )
    metrics["http_parser_healthy"] = bool(
        metrics.get("http_events", 0) > 0 or metrics.get("http_filter_has_metrics")
    )
    metrics["pbc_detection_healthy"] = metrics.get("max_pbc_in_graph", 0) > 0
    metrics["scoring_healthy"] = (
        metrics.get("max_has_signal", 0) > 0 or metrics.get("max_clusters", 0) > 0
    )
    metrics["overall_healthy"] = all([
        metrics["has_trace_output"],
        metrics["capture_loss_free"],
    ])

    return metrics


def _count_observer_calls(err_log_text: str) -> int:
    summary_match = re.search(r"Observer calls:\s*(\d+)", err_log_text)
    if summary_match:
        return int(summary_match.group(1))
    return len(re.findall(r"\[classify\]\s+Observer LLM:", err_log_text))


def _read_responsibility_db_metrics(db_path: Path) -> dict:
    metrics = {
        "responsibility_db_exists": db_path.exists(),
        "tls_sessions": 0,
        "process_events": 0,
        "graph_nodes": 0,
        "graph_edges": 0,
        "pbc_edges": 0,
        "correlation_evidence": 0,
        "responsibility_claims": 0,
        "responsibility_alerts": 0,
        "policy_evaluations": 0,
    }
    if not db_path.exists():
        return metrics

    try:
        with sqlite3.connect(str(db_path)) as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            queries = {
                "tls_sessions": ("tls_sessions", "SELECT COUNT(*) FROM tls_sessions"),
                "process_events": ("process_events", "SELECT COUNT(*) FROM process_events"),
                "graph_nodes": ("graph_nodes", "SELECT COUNT(*) FROM graph_nodes"),
                "graph_edges": ("graph_edges", "SELECT COUNT(*) FROM graph_edges"),
                "pbc_edges": (
                    "graph_edges",
                    "SELECT COUNT(*) FROM graph_edges WHERE edge_type='PolicyBoundaryCrossing'",
                ),
                "correlation_evidence": (
                    "correlation_evidence",
                    "SELECT COUNT(*) FROM correlation_evidence",
                ),
                "responsibility_claims": (
                    "responsibility_claims",
                    "SELECT COUNT(*) FROM responsibility_claims",
                ),
                "responsibility_alerts": (
                    "responsibility_alerts",
                    "SELECT COUNT(*) FROM responsibility_alerts",
                ),
                "policy_evaluations": (
                    "policy_evaluations",
                    "SELECT COUNT(*) FROM policy_evaluations",
                ),
            }
            for key, (table, query) in queries.items():
                if table in tables:
                    metrics[key] = int(conn.execute(query).fetchone()[0])
    except sqlite3.Error as exc:
        metrics["responsibility_db_error"] = str(exc)
    return metrics


def _read_stable_responsibility_db_metrics(
    db_path: Path,
    attempts: int = 4,
    delay_seconds: float = 0.2,
) -> dict:
    """Read responsibility DB metrics after writer shutdown has settled.

    The collector exits quickly after SIGINT, while SQLite/WAL visibility can lag
    by a very short interval on busy runs. For health summaries, prefer the last
    stable snapshot and keep monotonically increasing counters at their maximum.
    """
    last: dict | None = None
    best: dict | None = None
    count_keys = {
        "tls_sessions",
        "process_events",
        "graph_nodes",
        "graph_edges",
        "pbc_edges",
        "correlation_evidence",
        "responsibility_claims",
        "responsibility_alerts",
        "policy_evaluations",
    }
    for idx in range(max(attempts, 1)):
        current = _read_responsibility_db_metrics(db_path)
        if best is None:
            best = dict(current)
        else:
            for key, value in current.items():
                if key in count_keys:
                    best[key] = max(int(best.get(key, 0)), int(value or 0))
                else:
                    best[key] = value

        if last is not None and current == last:
            return current
        last = current
        if idx < attempts - 1:
            time.sleep(delay_seconds)
    return best or _read_responsibility_db_metrics(db_path)


# ═══════════════════════════════════════════════════════════════════════════════
#  CollectorManager
# ═══════════════════════════════════════════════════════════════════════════════

def collector_workspace_root(output_dir: Path) -> Path:
    """The agent's workspace for this run (the run directory).

    The collector is started from the harness isolation root, which is not the
    agent's working directory. Reporting it as the workspace made ordinary
    in-workspace writes look like capability expansions.
    """
    return Path(output_dir).resolve().parent


class CollectorManager(CollectorInterface):
    """MtAgentLens eBPF collector + tcpdump packet capture.

    Three-tier TLS capture per run:
    1. tls_capture eBPF uprobe (primary, real-time) — byte-pattern matching
       on BoringSSL functions for live SSL plaintext → Intent extraction.
    2. tcpdump pcap (backup, offline) — encrypted TCP for post-hoc verification.
    3. NODE_OPTIONS tls-keylog (backup) — TLS session keys for pcap decryption.

    Health verification (new in Phase 1):
    4. Uprobe attachment verification via /sys/kernel/debug/tracing/uprobe_events
    5. TLS canary test — sends known HTTPS request, verifies capture
    6. Runtime metrics — parsed from collector stderr on stop
    """

    def _effective_comm(self, claude_bin: str | None) -> str:
        """Return the process-comm filter that actually matches the agent.

        The interactive CLI is usually invoked as 'claude', but the real
        on-disk binary is 'claude.exe' and the kernel reports that basename as
        comm. A filter of only 'claude' therefore captures zero process
        events. Keep an explicitly requested capture-surface name untouched;
        for the generic default add the resolved binary basename.
        """
        requested = (
            os.environ.get("MTAGENTLENS_AGENT_COMM", "").strip() or self.comm.strip()
        )
        if requested and requested != "claude":
            return requested
        names = [requested] if requested else []
        if claude_bin:
            basename = Path(claude_bin).name
            if basename and basename not in names:
                names.append(basename)
        return ",".join(names) or "claude"

    def __init__(
        self,
        workspace: BenchmarkWorkspace,
        binary: str = "./collector/build/release/mtagentlens",
        subcommand: str = "trace",
        comm: str = "claude",
        trace_args: list[str] | None = None,
        log_file: str = "collector",
        sudo: bool = True,
        startup_check_seconds: float = 0.5,
        verify_uprobes: bool = True,
        uprobe_verify_timeout: float = 5.0,
        canary_test: bool = True,
        canary_timeout: float = 10.0,
        disable_responsibility_observer: bool = False,
    ):
        self._workspace = workspace
        self.binary = binary
        self.subcommand = subcommand
        self.comm = comm
        self.trace_args = trace_args or ["--ssl=true", "--process=true"]
        self.log_file = log_file
        self.sudo = sudo
        self.startup_check_seconds = startup_check_seconds
        self.verify_uprobes = verify_uprobes
        self.uprobe_verify_timeout = uprobe_verify_timeout
        self.canary_test = canary_test
        self.canary_timeout = canary_timeout
        self.disable_responsibility_observer = disable_responsibility_observer
        self._process: subprocess.Popen | None = None
        self._tcpdump: subprocess.Popen | None = None
        self._err_log = None
        self._output_dir: Path | None = None
        self._health_snapshot: dict = {}
        self._scenario_id: str | None = None
        self._run_mode: str | None = None

    def set_run_context(self, scenario_id: str, mode: str) -> None:
        self._scenario_id = scenario_id or None
        self._run_mode = mode or None

    # ── start / stop ───────────────────────────────────────────────────

    def start(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir = output_dir

        sudo_pass = os.environ.get("MTAGENTLENS_SUDO_PASSWORD", "")
        collector_env = dict(os.environ)
        collector_env["MTAGENTLENS_WORKSPACE_ROOT"] = str(
            collector_workspace_root(output_dir)
        )
        err_log = open(str(output_dir / f"{self.log_file}.log"), "w")
        self._err_log = err_log

        # ── 0. Resolve claude binary with diagnostics ──
        claude_bin, binary_diag = _find_claude_binary_with_diagnostics()
        self._health_snapshot["binary_detection"] = binary_diag
        self._health_snapshot["binary_path"] = claude_bin

        # ── 1. Start tcpdump for offline TLS decryption ──
        try:
            pcap_path = output_dir / "trace.pcap"
            tcpdump_log = open(str(output_dir / "tcpdump.log"), "w")
            tcpdump_cmd = [
                "tcpdump", "-i", "any", "-w", str(pcap_path), "-s", "0", "tcp",
            ]
            if self.sudo and sudo_pass:
                self._tcpdump = subprocess.Popen(
                    ["sudo", "-S"] + tcpdump_cmd,
                    stdin=subprocess.PIPE,
                    stdout=tcpdump_log, stderr=tcpdump_log,
                    start_new_session=True,
                )
                if self._tcpdump.stdin:
                    self._tcpdump.stdin.write((sudo_pass + "\n").encode())
                    self._tcpdump.stdin.flush()
            elif self.sudo:
                self._tcpdump = subprocess.Popen(
                    ["sudo", "-n"] + tcpdump_cmd,
                    stdout=tcpdump_log, stderr=tcpdump_log,
                    start_new_session=True,
                )
            else:
                self._tcpdump = subprocess.Popen(
                    tcpdump_cmd,
                    stdout=tcpdump_log, stderr=tcpdump_log,
                    start_new_session=True,
                )
        except Exception:
            self._tcpdump = None

        # ── 2. Start MtAgentLens collector ──
        binary_path = str(self._workspace.project_root / self.binary)
        log_path = output_dir / self.log_file
        # The comm filter is the capture-surface boundary. When the benchmark
        # launched the agent through a unique symlink, that basename (not the
        # interactive session's "claude") is what may be captured.
        effective_comm = self._effective_comm(claude_bin)
        self._health_snapshot["comm_filter"] = effective_comm
        configured_sources = _configured_capture_sources(self.trace_args)
        self._health_snapshot["trace_args"] = list(self.trace_args)
        self._health_snapshot["configured_capture_sources"] = sorted(configured_sources)
        collector_cmd = [
            binary_path, self.subcommand,
            "--comm", effective_comm,
            "-o", str(log_path),
        ] + self.trace_args + _run_context_args(self._scenario_id, self._run_mode)
        if self.disable_responsibility_observer:
            collector_cmd.append("--disable-responsibility-observer")

        # When SSL is enabled, pass --binary-path so tls_capture can attach
        # uprobes to Claude Code via byte-pattern matching (no symbols needed).
        ssl_enabled = "tls" in configured_sources
        if ssl_enabled:
            if claude_bin:
                collector_cmd.extend(["--binary-path", claude_bin])
                self._health_snapshot["ssl_binary_path_provided"] = claude_bin
            else:
                self._health_snapshot["ssl_binary_path_missing"] = True

        try:
            if self.sudo and sudo_pass:
                cmd = ["sudo", "-S"]
                preserve = [
                    "MTAGENTLENS_AGENT_COMMS",
                    "MTAGENTLENS_AGENT_CGROUP",
                    "MTAGENTLENS_AGENT_EXE",
                    "MTAGENTLENS_PROCESS_VERBOSE",
                ]
                preserve.append("MTAGENTLENS_WORKSPACE_ROOT")
                preserve.append("MTAGENTLENS_SENSITIVE_EGRESS")
                preserve.append("MTAGENTLENS_LOCAL_EGRESS")
                preserve.append("MTAGENTLENS_HARD_SENSITIVE_EGRESS")
                preserve.append("MTAGENTLENS_IGNORE_FILE_COMM_PREFIXES")
                if os.environ.get("MTAGENTLENS_PRESERVE_OBSERVER_ENV") == "1":
                    preserve.extend(
                        ["ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "MODEL"]
                    )
                cmd.append("--preserve-env=" + ",".join(preserve))
                cmd += collector_cmd
                self._process = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE,
                    stdout=err_log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=collector_env,
                )
                if self._process and self._process.stdin:
                    self._process.stdin.write((sudo_pass + "\n").encode())
                    self._process.stdin.flush()
            elif self.sudo:
                cmd = ["sudo", "-n"] + collector_cmd
                self._process = subprocess.Popen(
                    cmd, stdout=err_log, stderr=subprocess.STDOUT,
                    start_new_session=True, env=collector_env,
                )
            else:
                self._process = subprocess.Popen(
                    collector_cmd, stdout=err_log, stderr=subprocess.STDOUT,
                    start_new_session=True, env=collector_env,
                )
            self._check_collector_startup(output_dir / f"{self.log_file}.log")
        except Exception:
            self._stop_tcpdump()
            self._process = None
            raise

        # ── 3. Verify uprobe attachment ──
        if self.verify_uprobes and ssl_enabled and claude_bin:
            uprobe_diag = _verify_uprobes_attached(claude_bin, self.uprobe_verify_timeout)
            self._health_snapshot["uprobe_verification"] = uprobe_diag
            if not uprobe_diag.get("attached") and uprobe_diag.get("errors"):
                self._health_snapshot["uprobe_verification"]["warning"] = (
                    f"startup check did not observe an active uprobe for {claude_bin}; "
                    "final TLS capture health is determined from runtime metrics"
                )

        # ── 4. TLS canary test ──
        if self.canary_test and ssl_enabled and claude_bin:
            canary_diag = _tls_canary_test(
                output_dir,
                self.canary_timeout,
                self.log_file,
                comm_filter=effective_comm,
            )
            self._health_snapshot["canary_test"] = canary_diag

        # Write health snapshot for diagnostics
        (output_dir / "collector_health.json").write_text(
            json.dumps(self._health_snapshot, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def _database_has_final_verdict(self) -> bool:
        """Best-effort check that a canonical final verdict is already durable."""
        db_path = self.database_path
        if db_path is None or not db_path.exists():
            return False
        try:
            connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                rows = connection.execute(
                    "SELECT final_decision FROM runs WHERE final_decision IS NOT NULL"
                ).fetchall()
            finally:
                connection.close()
            return any(row[0] for row in rows)
        except (OSError, sqlite3.Error):
            return False

    def stop(self, wait_seconds: int | None = None) -> None:
        # Stop tcpdump first
        self._stop_tcpdump()
        if wait_seconds is None:
            # Phase-2 finalisation of a large multi-agent trace can take minutes.
            # A fixed short grace (historically 30 s) SIGKILLed the collector
            # before the canonical verdict existed, wasting the whole run.
            wait_seconds = int(os.environ.get(
                "MTAGENTLENS_COLLECTOR_STOP_TIMEOUT", "1800"
            ))

        # Stop collector with generous timeout for graceful shutdown.
        # The Rust collector's Phase 2 finalization (traceback, claims, alerts)
        # and WAL checkpoint can take minutes for large traces.
        if self._process is None:
            if self._output_dir:
                self._collect_runtime_metrics()
            return
        self._signal_process_group(self._process, signal.SIGINT)
        try:
            self._process.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            if self._database_has_final_verdict():
                # The verdict is already durable; the process is only finishing
                # teardown. Do not lose it, and do not call this a failed run.
                print("[WARNING] collector finalized the verdict but is still alive; terminating")
                self._signal_process_group(self._process, signal.SIGKILL)
                self._process.wait()
            else:
                grace = int(os.environ.get(
                    "MTAGENTLENS_COLLECTOR_FINALIZE_GRACE", "900"
                ))
                print(
                    f"[WARNING] collector did not exit within {wait_seconds}s; "
                    f"waiting {grace}s more for finalization before SIGKILL"
                )
                try:
                    self._process.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    print(
                        f"[ERROR] collector did not finalize within "
                        f"{wait_seconds + grace}s; sending SIGKILL"
                    )
                    self._signal_process_group(self._process, signal.SIGKILL)
                    self._process.wait()
        self._process = None
        if self._err_log is not None:
            self._err_log.close()
            self._err_log = None

        # Collect post-stop runtime metrics
        if self._output_dir:
            self._collect_runtime_metrics()
            self._write_health_snapshot()

        # ── Verify database integrity after shutdown ──
        if self._output_dir:
            self._verify_database_integrity()

    def _collect_runtime_metrics(self) -> None:
        """Collect runtime health metrics from collector output logs."""
        if not self._output_dir:
            return

        err_log = self._output_dir / f"{self.log_file}.log"
        trace_out = self._output_dir / self.log_file
        responsibility_db = self._output_dir / f"{self.log_file}.responsibility.db"

        if err_log.exists():
            try:
                text = err_log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
        else:
            text = ""

        configured_sources = _configured_capture_sources(self.trace_args)
        metrics = _parse_runtime_metrics_from_logs(
            text, trace_out, configured_sources
        )
        metrics["trace_args"] = list(self.trace_args)
        metrics["configured_capture_sources"] = sorted(configured_sources)
        db_metrics = _read_stable_responsibility_db_metrics(responsibility_db)
        metrics.update(db_metrics)

        metrics["tls_capture_active"] = bool(
            metrics.get("tls_sessions", 0) > 0
            or metrics.get("ssl_events", 0) > 0
        )
        metrics["process_monitor_active"] = metrics.get("process_events", 0) > 0
        metrics["responsibility_runtime_active"] = bool(
            metrics.get("graph_edges", 0) > 0
            or metrics.get("correlation_evidence", 0) > 0
            or metrics.get("policy_evaluations", 0) > 0
        )
        metrics["observer_active"] = "Responsibility Observer LLM enabled" in text
        metrics["observer_calls"] = _count_observer_calls(text)
        metrics["observer_used"] = metrics["observer_calls"] > 0

        metrics["ssl_uprobe_healthy"] = metrics["tls_capture_active"]
        metrics["pbc_detection_healthy"] = metrics.get("pbc_edges", 0) > 0
        metrics["scoring_healthy"] = bool(
            metrics.get("correlation_evidence", 0) > 0
            or metrics.get("graph_edges", 0) > 0
            or metrics.get("max_has_signal", 0) > 0
            or metrics.get("max_clusters", 0) > 0
        )
        # Startup checks must count. Previously overall_healthy was derived
        # purely from runtime DB metrics, so a collector whose uprobes never
        # attached still reported healthy as soon as any events showed up.
        #
        # "Not run" and "ran and failed" are distinguished: an absent check is
        # not penalised, but a check that executed and failed is.
        startup_ok = True
        startup_verified = False
        uprobe = self._health_snapshot.get("uprobe_verification") or {}
        if "tls" in configured_sources:
            startup_verified = bool(uprobe.get("attached"))
            if uprobe.get("errors") and not uprobe.get("attached"):
                startup_ok = False
        canary = self._health_snapshot.get("canary_test") or {}
        if "tls" in configured_sources and canary.get("sent") and canary.get("curl_ok"):
            startup_ok = startup_ok and bool(canary.get("captured_in_log"))
            startup_verified = bool(canary.get("captured_in_log"))
        metrics["startup_capture_verified"] = startup_verified and startup_ok

        active_sources = {
            "tls": metrics["tls_capture_active"],
            "process": metrics["process_monitor_active"],
            "stdio": metrics.get("stdio_events", 0) > 0,
        }
        metrics["overall_healthy"] = all([
            bool(configured_sources),
            all(active_sources[source] for source in configured_sources),
            metrics["responsibility_runtime_active"],
            metrics["capture_loss_free"],
            startup_ok,
        ])
        self._health_snapshot["runtime_metrics"] = metrics

    def _write_health_snapshot(self) -> None:
        """Persist the accumulated health snapshot to disk (post-stop update)."""
        if not self._output_dir:
            return
        snapshot_path = self._output_dir / "collector_health.json"
        try:
            snapshot_path.write_text(
                json.dumps(self._health_snapshot, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _verify_database_integrity(self) -> None:
        """Verify the SQLite database is intact after collector shutdown.

        Reports errors to stderr so they are visible in the run log.
        If the database is corrupt, the run should be retried.
        """
        db_path = self.database_path
        if db_path is None or not db_path.exists():
            print("[ERROR] responsibility database not found after collector stop")
            return
        # The collector writes the DB as root with 0600; this process usually
        # runs as the invoking user. Reading it directly produced
        # "integrity check raised: unable to open database file" on every run,
        # which the FPR summary then had to treat as observed anyway. Check a
        # readable copy instead of silently reporting a false failure.
        import tempfile

        check_path = db_path
        temp_dir = None
        try:
            if not os.access(db_path, os.R_OK):
                password = os.environ.get("MTAGENTLENS_SUDO_PASSWORD", "")
                sudo_args = ["sudo", "-S", "-p", ""] if password else ["sudo", "-n"]
                sudo_input = password + "\n" if password else None
                temp_dir = tempfile.TemporaryDirectory(prefix="mtl-integrity-")
                check_path = Path(temp_dir.name) / db_path.name
                copied = subprocess.run(
                    sudo_args + ["cp", str(db_path), str(check_path)],
                    input=sudo_input, text=True, capture_output=True, timeout=30,
                )
                if copied.returncode != 0:
                    print(f"[WARN] database integrity check skipped: cannot read {db_path} (sudo copy failed)")
                    return
                owned = subprocess.run(
                    sudo_args + ["chown", f"{os.getuid()}:{os.getgid()}", str(check_path)],
                    input=sudo_input, text=True, capture_output=True, timeout=30,
                )
                if owned.returncode != 0:
                    print("[WARN] database integrity check skipped: cannot access temporary copy")
                    return
            con = sqlite3.connect(f"{check_path.resolve().as_uri()}?mode=ro", uri=True)
            try:
                result = con.execute("PRAGMA integrity_check").fetchone()
                fk_result = con.execute("PRAGMA foreign_key_check").fetchone()
            finally:
                con.close()
            integrity_ok = result is not None and str(result[0]).lower() == "ok"
            fk_ok = fk_result is None
            if integrity_ok and fk_ok:
                print(f"[OK] database integrity verified: {db_path}")
            else:
                details = []
                if not integrity_ok:
                    details.append(f"integrity_check: {result}")
                if not fk_ok:
                    details.append(f"foreign_key_check: {fk_result}")
                print(f"[ERROR] database integrity FAILED: {'; '.join(details)}")
        except Exception as error:
            print(f"[ERROR] database integrity check raised: {error}")
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

    def _check_collector_startup(self, err_log_path: Path) -> None:
        """Fail fast if the collector exits before the attach window begins."""
        deadline = time.time() + max(self.startup_check_seconds, 0.0)
        while True:
            if self._process is None:
                raise RuntimeError("collector process was not created")
            exit_code = self._process.poll()
            if exit_code is not None:
                tail = _read_tail(err_log_path)
                detail = f": {tail}" if tail else ""
                raise RuntimeError(
                    f"collector exited during startup with status {exit_code}{detail}"
                )
            if time.time() >= deadline:
                return
            time.sleep(min(0.05, max(deadline - time.time(), 0.0)))

    def _stop_tcpdump(self) -> None:
        if self._tcpdump is not None:
            try:
                self._signal_process_group(self._tcpdump, signal.SIGTERM)
                self._tcpdump.wait(timeout=3)
            except Exception:
                try:
                    self._signal_process_group(self._tcpdump, signal.SIGKILL)
                except Exception:
                    pass
            self._tcpdump = None

    @staticmethod
    def _signal_process_group(proc: subprocess.Popen, sig: int) -> None:
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            proc.send_signal(sig)

    # ── properties ─────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def output_paths(self) -> list[Path]:
        if self._output_dir is None:
            return []
        paths = [self._output_dir / self.log_file]
        db = self._output_dir / f"{self.log_file}.responsibility.db"
        if db.exists():
            paths.append(db)
        return paths

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    @property
    def database_path(self) -> Path | None:
        if self._output_dir is None:
            return None
        return self._output_dir / f"{self.log_file}.responsibility.db"

    @property
    def health_snapshot(self) -> dict:
        return dict(self._health_snapshot)


def _read_tail(path: Path, limit: int = 1200) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:].strip()
