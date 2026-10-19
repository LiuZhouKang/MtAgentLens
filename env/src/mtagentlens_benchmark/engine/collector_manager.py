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
    """True if the collector log shows the canary request was actually seen.

    The canary URL carries the unique marker ``mtagentlens-canary``, so a
    substring hit is unambiguous evidence that this TLS request was captured.
    """
    log_path = output_dir / f"{log_file}.log"
    if not log_path.exists():
        return False
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "mtagentlens-canary" in text


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

def _parse_runtime_metrics_from_logs(
    err_log_text: str,
    trace_out_path: Path,
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

    # SSL events (indicates tls_capture uprobe is working)
    ssl_pattern = re.compile(r'"event":"SSL"')
    metrics["ssl_events"] = len(ssl_pattern.findall(err_log_text))

    # HTTP events (indicates HTTP parser is receiving data)
    http_pattern = re.compile(r'"event":"HTTP"')
    metrics["http_events"] = len(http_pattern.findall(err_log_text))

    # Ring buffer drops
    drop_pattern = re.compile(r'"dropped_events":(\d+)')
    drops = [int(m) for m in drop_pattern.findall(err_log_text)]
    metrics["ring_buffer_drops"] = sum(drops)
    metrics["ring_buffer_drop_events"] = len(drops)

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
        metrics["pbc_detection_healthy"],
        metrics["scoring_healthy"],
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

    # ── start / stop ───────────────────────────────────────────────────

    def start(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir = output_dir

        sudo_pass = os.environ.get("MTAGENTLENS_SUDO_PASSWORD", "")
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
        effective_comm = (
            os.environ.get("MTAGENTLENS_AGENT_COMM", "").strip()
            or self.comm
            or (Path(claude_bin).name if claude_bin else "claude")
        )
        self._health_snapshot["comm_filter"] = effective_comm
        collector_cmd = [
            binary_path, self.subcommand,
            "--comm", effective_comm,
            "-o", str(log_path),
        ] + self.trace_args
        if self.disable_responsibility_observer:
            collector_cmd.append("--disable-responsibility-observer")

        # When SSL is enabled, pass --binary-path so tls_capture can attach
        # uprobes to Claude Code via byte-pattern matching (no symbols needed).
        ssl_enabled = "--ssl=true" in self.trace_args or "--ssl" in self.trace_args
        ssl_explicit_disabled = "--ssl=false" in self.trace_args
        if ssl_enabled and not ssl_explicit_disabled:
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
                ]
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
                )
                if self._process and self._process.stdin:
                    self._process.stdin.write((sudo_pass + "\n").encode())
                    self._process.stdin.flush()
            elif self.sudo:
                cmd = ["sudo", "-n"] + collector_cmd
                self._process = subprocess.Popen(
                    cmd, stdout=err_log, stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            else:
                self._process = subprocess.Popen(
                    collector_cmd, stdout=err_log, stderr=subprocess.STDOUT,
                    start_new_session=True,
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

    def stop(self, wait_seconds: int = 30) -> None:
        # Stop tcpdump first
        self._stop_tcpdump()

        # Stop collector with generous timeout for graceful shutdown.
        # The Rust collector's Phase 2 finalization (traceback, claims, alerts)
        # and WAL checkpoint can take 10-20 seconds for large traces.
        if self._process is None:
            if self._output_dir:
                self._collect_runtime_metrics()
            return
        self._signal_process_group(self._process, signal.SIGINT)
        try:
            self._process.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            # Graceful shutdown timed out — force kill but log a warning
            print(f"[WARNING] collector did not exit within {wait_seconds}s, sending SIGKILL")
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
        trace_out = self._output_dir / f"{self.log_file}.log"
        responsibility_db = self._output_dir / f"{self.log_file}.responsibility.db"

        if err_log.exists():
            try:
                text = err_log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
        else:
            text = ""

        metrics = _parse_runtime_metrics_from_logs(text, trace_out)
        db_metrics = _read_stable_responsibility_db_metrics(responsibility_db)
        metrics.update(db_metrics)

        metrics["tls_capture_active"] = bool(
            metrics.get("tls_sessions", 0) > 0
            or "BoringSSL detected! Attaching by offset" in text
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
        uprobe = self._health_snapshot.get("uprobe_verification") or {}
        if uprobe:
            startup_ok = startup_ok and bool(uprobe.get("attached"))
        canary = self._health_snapshot.get("canary_test") or {}
        if canary.get("sent") and canary.get("curl_ok"):
            startup_ok = startup_ok and bool(canary.get("captured_in_log"))
        metrics["startup_capture_verified"] = startup_ok

        metrics["overall_healthy"] = all([
            metrics["tls_capture_active"],
            metrics["process_monitor_active"],
            metrics["responsibility_runtime_active"],
            metrics["pbc_detection_healthy"],
            metrics["scoring_healthy"],
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
        try:
            con = sqlite3.connect(str(db_path))
            # integrity_check returns "ok" on success, error details otherwise
            result = con.execute("PRAGMA integrity_check").fetchone()
            fk_result = con.execute("PRAGMA foreign_key_check").fetchone()
            con.close()
            integrity_ok = result is not None and str(result[0]).lower() == "ok"
            fk_ok = fk_result is None  # None means no violations
            if integrity_ok and fk_ok:
                print(f"[OK] database integrity verified: {db_path}")
            else:
                details = []
                if not integrity_ok:
                    details.append(f"integrity_check: {result}")
                if not fk_ok:
                    details.append(f"foreign_key_check: {fk_result}")
                print(f"[ERROR] database integrity FAILED: {'; '.join(details)}")
        except Exception as e:
            print(f"[ERROR] database integrity check raised: {e}")

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
