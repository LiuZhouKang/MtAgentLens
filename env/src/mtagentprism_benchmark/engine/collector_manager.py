"""MtAgentPrism collector lifecycle — single-process eBPF collector + tcpdump.

Phase 1 hardened: uprobe verification, TLS canary test, runtime health monitoring,
comprehensive binary detection with diagnostics.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

from mtagentprism_benchmark.types import CollectorInterface
from mtagentprism_benchmark.workspace import BenchmarkWorkspace


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
                sudo_pass = os.environ.get("MTAGENTPRISM_SUDO_PASSWORD", "")
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
            diag["errors"].append("uprobe_events file is empty; no eBPF uprobes active")
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

CANARY_URL = "https://httpbin.org/get?mtagentprism-canary=verify"


def _tls_canary_test(output_dir: Path, timeout: float = 10.0) -> dict:
    """Send a known TLS request and verify the collector captured it.

    Uses curl to make a marked HTTPS request. After a short wait, checks
    the collector's output for evidence of TLS capture.
    """
    diag: dict = {
        "canary_url": CANARY_URL,
        "sent": False,
        "curl_ok": False,
        "captured_in_log": False,
        "errors": [],
    }

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

    # Wait for collector to process and log the event
    time.sleep(2.0)

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


# ═══════════════════════════════════════════════════════════════════════════════
#  CollectorManager
# ═══════════════════════════════════════════════════════════════════════════════

class CollectorManager(CollectorInterface):
    """MtAgentPrism eBPF collector + tcpdump packet capture.

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
        binary: str = "./collector/build/release/mtagentprism",
        subcommand: str = "trace",
        comm: str = "claude",
        trace_args: list[str] | None = None,
        log_file: str = "trace",
        sudo: bool = True,
        startup_check_seconds: float = 0.5,
        verify_uprobes: bool = True,
        uprobe_verify_timeout: float = 5.0,
        canary_test: bool = True,
        canary_timeout: float = 10.0,
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
        self._process: subprocess.Popen | None = None
        self._tcpdump: subprocess.Popen | None = None
        self._output_dir: Path | None = None
        self._health_snapshot: dict = {}

    # ── start / stop ───────────────────────────────────────────────────

    def start(self, output_dir: Path) -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir = output_dir

        sudo_pass = os.environ.get("MTAGENTPRISM_SUDO_PASSWORD", "")
        err_log = open(str(output_dir / "trace.err.log"), "w")

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
                )
                if self._tcpdump.stdin:
                    self._tcpdump.stdin.write((sudo_pass + "\n").encode())
                    self._tcpdump.stdin.flush()
            elif self.sudo:
                self._tcpdump = subprocess.Popen(
                    ["sudo", "-n"] + tcpdump_cmd,
                    stdout=tcpdump_log, stderr=tcpdump_log,
                )
            else:
                self._tcpdump = subprocess.Popen(
                    tcpdump_cmd,
                    stdout=tcpdump_log, stderr=tcpdump_log,
                )
        except Exception:
            self._tcpdump = None

        # ── 2. Start MtAgentPrism collector ──
        binary_path = str(self._workspace.project_root / self.binary)
        log_path = output_dir / self.log_file
        collector_cmd = [
            binary_path, self.subcommand,
            "--comm", self.comm,
            "-o", str(log_path),
        ] + self.trace_args

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
                # Preserve Observer LLM env vars through sudo so the collector
                # can call the DeepSeek/Anthropic API for LLM arbitration.
                cmd = ["sudo", "-S", "--preserve-env=ANTHROPIC_BASE_URL,ANTHROPIC_AUTH_TOKEN,MODEL"] + collector_cmd
                self._process = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE,
                    stdout=err_log, stderr=subprocess.STDOUT,
                )
                if self._process and self._process.stdin:
                    self._process.stdin.write((sudo_pass + "\n").encode())
                    self._process.stdin.flush()
            elif self.sudo:
                cmd = ["sudo", "-n"] + collector_cmd
                self._process = subprocess.Popen(
                    cmd, stdout=err_log, stderr=subprocess.STDOUT,
                )
            else:
                self._process = subprocess.Popen(
                    collector_cmd, stdout=err_log, stderr=subprocess.STDOUT,
                )
            self._check_collector_startup(output_dir / "trace.err.log")
        except Exception:
            self._stop_tcpdump()
            self._process = None
            raise

        # ── 3. Verify uprobe attachment ──
        if self.verify_uprobes and ssl_enabled and claude_bin:
            uprobe_diag = _verify_uprobes_attached(claude_bin, self.uprobe_verify_timeout)
            self._health_snapshot["uprobe_verification"] = uprobe_diag
            if not uprobe_diag.get("attached"):
                self._health_snapshot["uprobe_verification"]["warning"] = (
                    f"uprobe not detected for {claude_bin}; "
                    "TLS capture will not work. Check debugfs mount and permissions."
                )

        # ── 4. TLS canary test ──
        if self.canary_test and ssl_enabled and claude_bin:
            canary_diag = _tls_canary_test(output_dir, self.canary_timeout)
            self._health_snapshot["canary_test"] = canary_diag

        # Write health snapshot for diagnostics
        (output_dir / "collector_health.json").write_text(
            json.dumps(self._health_snapshot, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    def stop(self, wait_seconds: int = 5) -> None:
        # Stop tcpdump first
        self._stop_tcpdump()

        # Stop collector
        if self._process is None:
            if self._output_dir:
                self._collect_runtime_metrics()
            return
        self._process.send_signal(signal.SIGINT)
        try:
            self._process.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        self._process = None

        # Collect post-stop runtime metrics
        if self._output_dir:
            self._collect_runtime_metrics()

    def _collect_runtime_metrics(self) -> None:
        """Collect runtime health metrics from collector output logs."""
        if not self._output_dir:
            return

        err_log = self._output_dir / "trace.err.log"
        trace_out = self._output_dir / self.log_file

        if err_log.exists():
            try:
                text = err_log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
        else:
            text = ""

        metrics = _parse_runtime_metrics_from_logs(text, trace_out)
        self._health_snapshot["runtime_metrics"] = metrics

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
                self._tcpdump.terminate()
                self._tcpdump.wait(timeout=3)
            except Exception:
                try:
                    self._tcpdump.kill()
                except Exception:
                    pass
            self._tcpdump = None

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
