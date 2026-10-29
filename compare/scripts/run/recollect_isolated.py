#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0 OR MIT
"""P1.2 — recollect the 86 FuxiBench scenarios with the delivered binary.

Why this exists
---------------
Every database under ``datasets/FuxiBench/traces`` was written by the
2026-07 collector build, before the graph-pattern producer was restored and
before the credential resource producer existed. Pattern verdicts and
Credential nodes are therefore zero in every scenario: that is "the build did
not produce them", not "the detector correctly found nothing".

Protocol
--------
Each scenario runs with the same isolation as the benign FPR baseline
(``docs/evaluation/measurement-isolation.md``): clean profile, an
inode-distinct agent copy, a short unique comm, and the responsibility
observer disabled so a failed external API call cannot decide a verdict. The
run's ``collector.responsibility.db`` replaces the corpus copy only after the
run exits 0. The old corpus is backed up once under ``local/`` (gitignored).

Usage
-----
    python3 compare/scripts/run/recollect_isolated.py [--only SID] [--no-skip]
                                                      [--timeout 900]

The script is resumable: completed scenario ids are read from
``compare/results/p12-recollect/results.jsonl`` and skipped.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "compare" / "Dataset" / "FuxiBench" / "prompt_inputs" / "manifest.json"
TRACES = ROOT / "compare" / "Dataset" / "FuxiBench" / "traces"
RUNS = ROOT / "env" / "runs"
REPORT_DIR = ROOT / "compare" / "results" / "p12-recollect"
RESULTS = REPORT_DIR / "results.jsonl"
SUMMARY = REPORT_DIR / "summary.json"
BINARY = ROOT / "collector" / "build" / "release" / "mtagentlens"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denominator = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, center - half), min(1.0, center + half))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sudo_copy(path: Path, dest: Path) -> None:
    """Copy a root-0600 file to ``dest`` and give it to the caller."""
    tmpdir = Path(tempfile.mkdtemp(prefix="mtl-p12-db-"))
    tmp = tmpdir / path.name
    password = os.environ.get("MTAGENTLENS_SUDO_PASSWORD", "")

    def sudo(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["sudo", "-S", "-p", "", *args],
            input=password + "\n",
            text=True,
            capture_output=True,
        )

    proc = sudo("cp", str(path), str(tmp))
    if proc.returncode != 0:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise RuntimeError(f"sudo cp failed for {path}: {proc.stderr.strip()}")
    chown = sudo("chown", f"{os.getuid()}:{os.getgid()}", str(tmp))
    if chown.returncode != 0:
        shutil.rmtree(tmpdir, ignore_errors=True)
        raise RuntimeError(f"sudo chown failed for {path}: {chown.stderr.strip()}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tmp, dest)
    shutil.rmtree(tmpdir, ignore_errors=True)


def scenario_mode(source: Path) -> str:
    """Mirror env/run_one.py: only multi-agent JSON needs tmux panes."""
    data = json.loads(source.read_text())
    agents = data.get("agents") or []
    if data.get("mode") == "single_agent" or len(agents) <= 1:
        return "process"
    return "tmux-live"


def scenario_file(scenario_id: str) -> Path | None:
    matches = list((ROOT / "env" / "scenarios").rglob(f"{scenario_id}.json"))
    return matches[0] if matches else None


def corpus_path(entry: dict) -> Path:
    """Where this scenario's DB lives (or will live) in the corpus."""
    sid = entry["scenario_id"]
    for candidate in TRACES.rglob("responsibility.db"):
        if candidate.parent.name == sid:
            return candidate
    return TRACES / entry["category"] / sid / "responsibility.db"


def newest_run_dir(scenario_id: str, started_at: float) -> Path | None:
    candidates = []
    if not RUNS.exists():
        return None
    for run_dir in RUNS.iterdir():
        if not run_dir.is_dir():
            continue
        marker = run_dir / ".scenario_id"
        try:
            if marker.read_text().strip() != scenario_id:
                continue
        except OSError:
            continue
        if run_dir.stat().st_mtime + 5 < started_at:
            continue
        candidates.append(run_dir)
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def database_stats(db_path: Path) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        stats: dict = {
            "alerts_by_decision": {},
            "alerts_by_severity": {},
            "claims_by_type": {},
            "pattern_verdicts": 0,
            "credential_nodes": 0,
            "intents": 0,
            "actions": 0,
            "evidence": 0,
        }
        if "responsibility_alerts" in tables:
            for decision, severity, engine_raw in conn.execute(
                "SELECT decision, severity, engine_section_json "
                "FROM responsibility_alerts"
            ):
                stats["alerts_by_decision"][str(decision)] = (
                    stats["alerts_by_decision"].get(str(decision), 0) + 1
                )
                stats["alerts_by_severity"][str(severity)] = (
                    stats["alerts_by_severity"].get(str(severity), 0) + 1
                )
                if engine_raw:
                    try:
                        engine = json.loads(engine_raw) or {}
                        stats["pattern_verdicts"] += len(
                            engine.get("pattern_verdicts") or []
                        )
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass
        if "responsibility_claims" in tables:
            for claim_type, count in conn.execute(
                "SELECT claim_type, COUNT(*) FROM responsibility_claims GROUP BY 1"
            ):
                stats["claims_by_type"][str(claim_type)] = count
        if "graph_nodes" in tables:
            # Match the JSON key, not the bare word: a fixture path such as
            # `.../credential-theft-direct/docs.md` contains "credential" but
            # is a File resource.
            stats["credential_nodes"] = conn.execute(
                "SELECT COUNT(*) FROM graph_nodes "
                "WHERE node_type = 'Credential' OR resource_json LIKE '%\"Credential\"%'"
            ).fetchone()[0]
        for table, key in (
            ("intents", "intents"),
            ("actions", "actions"),
            ("correlation_evidence", "evidence"),
        ):
            if table in tables:
                stats[key] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        stats["observed"] = (
            stats["intents"] > 0 or stats["actions"] > 0 or stats["evidence"] > 0
        )
        return stats
    finally:
        conn.close()


def label_counts(entry: dict) -> str:
    return "benign" if entry.get("attack_type") == "benign" else "attack"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default=None, help="run one scenario id")
    parser.add_argument("--no-skip", action="store_true", help="re-run completed ids")
    parser.add_argument(
        "--db-dir",
        default=None,
        help="copy collected DBs here instead of overwriting the corpus (sample runs)",
    )
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    if not BINARY.exists():
        print(f"ERROR: release binary missing: {BINARY}", file=sys.stderr)
        return 2
    binary_sha = sha256_file(BINARY)
    print(f"[binary] {BINARY} sha256={binary_sha}")

    manifest = json.loads(MANIFEST.read_text())
    entries = manifest["entries"]
    if args.only:
        entries = [entry for entry in entries if entry["scenario_id"] == args.only]
        if not entries:
            print(f"ERROR: scenario not in manifest: {args.only}", file=sys.stderr)
            return 2

    # One backup, before the first overwrite. The corpus is gitignored and a
    # recollection destroys the only copy of the 2026-07 build's output.
    backup = ROOT / "local" / "corpus-backup-2026-10-19-pre-p12" / "traces"
    if not backup.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)
        print(f"[backup] {TRACES} -> {backup}")
        shutil.copytree(TRACES, backup)
    else:
        print(f"[backup] existing {backup}")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()
    if RESULTS.exists() and not args.no_skip:
        for line in RESULTS.read_text().splitlines():
            try:
                row = json.loads(line)
                if row.get("copied"):
                    done.add(row["scenario_id"])
            except (ValueError, KeyError):
                pass
        print(f"[resume] {len(done)} scenario(s) already copied")

    benchmark = shutil.which("mtagentlens-benchmark") or str(
        ROOT / "env" / ".venv" / "bin" / "mtagentlens-benchmark"
    )
    from mtagentlens_benchmark.engine.clean_profile import isolated_agent

    results: list[dict] = []
    failures: list[dict] = []
    with isolated_agent() as agent:
        print(f"[isolation] binary={agent.binary} comm={agent.comm}")
        for index, entry in enumerate(entries, 1):
            sid = entry["scenario_id"]
            if sid in done:
                print(f"[skip] {sid}")
                continue
            source = scenario_file(sid)
            if source is None:
                failures.append({"scenario_id": sid, "error": "scenario json missing"})
                print(f"[FAIL] {sid}: scenario json missing")
                continue
            mode = scenario_mode(source)
            cmd = [
                benchmark,
                "run",
                "--scenario",
                sid,
                "--mode",
                mode,
                "--collector",
                "--clean-profile",
                "--disable-responsibility-observer",
            ]
            if agent.binary:
                cmd.extend(["--agent-binary", agent.binary, "--agent-comm", agent.comm])
            env = agent.apply(os.environ.copy())
            env["MTAGENTLENS_CLEAN_PROFILE"] = "1"
            env.setdefault("MTAGENTLENS_SUDO_PASSWORD", "")
            started = time.time()
            print(f"[{index}/{len(entries)}] {sid} mode={mode}")
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(ROOT),
                    capture_output=True,
                    text=True,
                    timeout=args.timeout,
                    env=env,
                )
                rc = proc.returncode
                stderr_tail = proc.stderr[-2000:]
                stdout_tail = proc.stdout[-2000:]
            except subprocess.TimeoutExpired as exc:
                rc = -1
                stderr_tail = f"timeout after {args.timeout}s: {exc}"
                stdout_tail = ""
            duration = round(time.time() - started, 1)

            run_dir = newest_run_dir(sid, started)
            row: dict = {
                "scenario_id": sid,
                "category": entry["category"],
                "label": label_counts(entry),
                "mode": mode,
                "returncode": rc,
                "duration_seconds": duration,
                "run_dir": str(run_dir) if run_dir else None,
                "scenario_path": str(source),
                "scenario_sha256": sha256_file(source),
                "binary_sha256": binary_sha,
                "observer": "disabled",
                "copied": False,
                "stderr_tail": stderr_tail,
                "stdout_tail": stdout_tail,
            }
            if rc == 0 and run_dir is not None:
                db = run_dir / "collector" / "collector.responsibility.db"
                if db.exists():
                    dest = (
                        Path(args.db_dir) / f"{sid}.db"
                        if args.db_dir
                        else corpus_path(entry)
                    )
                    try:
                        sudo_copy(db, dest)
                        row["db_path"] = str(dest)
                        row["db_sha256"] = sha256_file(dest)
                        row.update(database_stats(dest))
                        row["copied"] = True
                    except Exception as exc:  # noqa: BLE001 - reported, not hidden
                        row["error"] = f"copy failed: {exc}"
                        failures.append({"scenario_id": sid, "error": row["error"]})
                else:
                    row["error"] = "collector.responsibility.db missing"
                    failures.append({"scenario_id": sid, "error": row["error"]})
            else:
                failures.append(
                    {"scenario_id": sid, "error": f"rc={rc}", "stderr": stderr_tail[-500:]}
                )
            with RESULTS.open("a") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            results.append(row)
            status = "OK" if row["copied"] else "FAIL"
            print(
                f"    {status} rc={rc} {duration}s "
                f"patterns={row.get('pattern_verdicts')} "
                f"credentials={row.get('credential_nodes')} "
                f"claims={sum((row.get('claims_by_type') or {}).values())}"
            )

    # ── Summary over the whole report (resume-safe) ──────────────────
    all_rows: list[dict] = []
    if RESULTS.exists():
        all_rows = [
            json.loads(line) for line in RESULTS.read_text().splitlines() if line.strip()
        ]
    copied = [row for row in all_rows if row.get("copied") and row.get("observed")]
    benign = [row for row in copied if row["label"] == "benign"]
    attack = [row for row in copied if row["label"] == "attack"]

    def rate(rows: list[dict], predicate) -> dict:
        hits = sum(1 for row in rows if predicate(row))
        low, high = wilson(hits, len(rows))
        return {
            "hits": hits,
            "n": len(rows),
            "rate": round(hits / len(rows), 4) if rows else None,
            "wilson_95": [round(low, 4), round(high, 4)],
        }

    summary = {
        "schema": "mtagentlens.p12-recollect/1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "binary_sha256": binary_sha,
        "protocol": {
            "collector": "release binary, --clean-profile, --disable-responsibility-observer",
            "isolation": "inode-distinct agent copy + <=15-byte unique comm",
            "corpus": "datasets/FuxiBench/traces (backup in local/corpus-backup-2026-10-19-pre-p12)",
        },
        "scenarios_total": len(manifest["entries"]),
        "scenarios_copied": sum(1 for row in all_rows if row.get("copied")),
        "scenarios_observed": len(copied),
        "failures": failures,
        "pattern_verdicts": {
            "benign": rate(benign, lambda row: row.get("pattern_verdicts", 0) > 0),
            "attack": rate(attack, lambda row: row.get("pattern_verdicts", 0) > 0),
        },
        "credential_nodes": {
            "benign": rate(benign, lambda row: row.get("credential_nodes", 0) > 0),
            "attack": rate(attack, lambda row: row.get("credential_nodes", 0) > 0),
        },
        "tainted_context_use": {
            "benign": rate(
                benign,
                lambda row: (row.get("claims_by_type") or {}).get(
                    "TaintedContextUse", 0
                )
                > 0,
            ),
            "attack": rate(
                attack,
                lambda row: (row.get("claims_by_type") or {}).get(
                    "TaintedContextUse", 0
                )
                > 0,
            ),
        },
        "unsafe": {
            "benign": rate(
                benign,
                lambda row: (row.get("alerts_by_decision") or {}).get("Unsafe", 0) > 0,
            ),
            "attack": rate(
                attack,
                lambda row: (row.get("alerts_by_decision") or {}).get("Unsafe", 0) > 0,
            ),
        },
    }
    SUMMARY.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(f"[summary] {SUMMARY}")
    print(
        f"copied {summary['scenarios_copied']}/{summary['scenarios_total']}, "
        f"observed {summary['scenarios_observed']}, failures {len(failures)}"
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
