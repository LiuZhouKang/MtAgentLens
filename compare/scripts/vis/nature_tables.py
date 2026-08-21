#!/usr/bin/env python3
"""Nature-style LaTeX tables for completed MtAgentLens paper artifacts."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[3]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from compare.scripts.vis.paper_report import (  # noqa: E402
    PaperReportError,
    dataset_counts,
    load_completed_artifact,
    load_paper_report_v1,
    prompt_mcnemar_rows,
    unsupported_claims,
)

RESULTS = PROJECT / "compare" / "results" / "paper"
PERF_DIR = PROJECT / "compare" / "results" / "perf"
BACKEND_HEALTH_DIR = PROJECT / "compare" / "results" / "backend_health"
TOOL_ORDER = ["mtagentlens", "llm-guard", "meta-prompt-guard", "vigil"]
TOOL_SHORT = {
    "mtagentlens": "MtAgentLens",
    "llm-guard": "LLM Guard",
    "meta-prompt-guard": "Meta Prompt Guard",
    "vigil": "Vigil",
}


LATEX_PREAMBLE = r"""
\usepackage{booktabs}
\usepackage{threeparttable}
\usepackage{siunitx}
\usepackage[table]{xcolor}
\definecolor{aspale}{HTML}{F0F2F6}
\newcommand{\asrow}{\rowcolor{aspale}}
""".strip()


def table_main(report):
    """Build the main evaluation table.

    When ``per_type_metrics`` is present in the report the table includes
    per-category (Type A / Type B / Type C) recall columns alongside the
    overall recall and F1.  Otherwise it falls back to the
    traditional confusion-matrix format (TP / FP / precision / recall).
    """
    metrics = report.get("metrics_by_tool", {})
    per_type = report.get("per_type_metrics", {})
    total, attack, benign = dataset_counts(report)

    # Detect whether per-type breakdown is available (keys are "A" / "B" / "C").
    has_per_type = any(
        tool_id in per_type and isinstance(per_type[tool_id], dict)
        and per_type[tool_id].get("C")
        for tool_id in TOOL_ORDER
    )

    if has_per_type:
        headers = [
            "工具",
            "Type A", "Type B", "Type C",
            "Overall", "F1",
        ]
    else:
        headers = [
            "工具",
            "TP", "FP",
            "精确率", "召回率", "F1",
            "场景召回",
        ]

    # Per-type key mapping (report uses "A"/"B"/"C", table uses "Type A"/"Type B"/"Type C").
    _PER_TYPE_KEYS = ("A", "B", "C")

    rows = []
    for tool_id in TOOL_ORDER:
        m = metrics.get(tool_id)
        if m is None:
            continue
        prefix = "\\asrow " if tool_id == "mtagentlens" else ""
        if has_per_type:
            pt = per_type.get(tool_id, {})
            per_type_recalls = [
                f"{float(pt.get(k, {}).get('recall', 0.0)):.3f}"
                for k in _PER_TYPE_KEYS
            ]
            rows.append([
                prefix + TOOL_SHORT.get(tool_id, tool_id),
                *per_type_recalls,
                f"{float(m.get('recall', 0.0)):.3f}",
                f"{float(m.get('f1', 0.0)):.3f}",
            ])
        else:
            rows.append([
                prefix + TOOL_SHORT.get(tool_id, tool_id),
                str(int(m.get("tp", 0))),
                str(int(m.get("fp", 0))),
                f"{float(m.get('precision', 0.0)):.3f}",
                f"{float(m.get('recall', 0.0)):.3f}",
                f"{float(m.get('f1', 0.0)):.3f}",
                f"{float(m.get('scenario_recall', m.get('recall', 0.0))):.3f}",
            ])
    return _table(
        headers,
        rows,
        f"Completed main evaluation on FuxiBench ($n={total}$; {attack} attack, {benign} benign).",
        "tab:main_completed",
        notes="Only completed frozen MtAgentLens and prompt-baseline artifacts are included.",
    )


def table_heatmap(report):
    return ""


def table_mcnemar(report):
    headers = ["Comparison", "$b$", "$c$", "Yates statistic", "$p$ value"]
    rows = [
        [
            f"MtAgentLens vs {TOOL_SHORT.get(row['tool_id'], row['tool_id'])}",
            str(row["b"]),
            str(row["c"]),
            f"{row['statistic']:.2f}",
            f"{row['p_value']:.3e}",
        ]
        for row in prompt_mcnemar_rows(report)
    ]
    return _table(
        headers,
        rows,
        "Scenario-level paired McNemar tests against prompt baselines.",
        "tab:mcnemar_completed",
        notes="b: MtAgentLens correct and prompt baseline wrong; c: prompt baseline correct and MtAgentLens wrong.",
    )


def table_ablation(report):
    try:
        artifact = load_completed_artifact(
            RESULTS / "controlled_ablation_results.json",
            expected_schema="mtagentlens.controlled_ablation_results.v1",
        )
    except PaperReportError:
        return ""
    baseline = artifact.get("baseline", {})
    baseline_metrics = baseline.get("metrics", {}) if isinstance(baseline, dict) else {}
    headers = ["Variant", "Disabled features", "Runs", "Scenario recall"]
    rows = []
    if baseline_metrics:
        rows.append([
            "\\asrow Full replay",
            "None",
            str(int(baseline_metrics.get("runs_completed", 0))),
            _fmt3(baseline_metrics.get("scenario_recall")),
        ])
    for variant in artifact.get("variants", []):
        if not isinstance(variant, dict) or variant.get("status") != "completed":
            continue
        metrics = variant.get("metrics", {})
        rows.append([
            _escape_latex(str(variant.get("label") or variant.get("variant_id", ""))),
            _escape_latex(", ".join(variant.get("disabled_features", [])[:3])),
            str(int(variant.get("runs_completed", 0))),
            _fmt3(metrics.get("scenario_recall")),
        ])
    return _table(
        headers,
        rows,
        "Controlled persisted-graph action-family feature-flag ablation over 67 frozen scenarios.",
        "tab:controlled_ablation_completed",
        notes="This table supports SQLite persisted-graph action-family replay only; it is not a live module-level ablation.",
    )


def table_backend_health(report=None):
    try:
        artifact = load_completed_artifact(
            BACKEND_HEALTH_DIR / "capture_health.json",
            expected_schema="mtagentlens.backend_capture_health.v1",
        )
    except PaperReportError:
        return ""
    runs = artifact.get("runs", [])
    run = runs[0] if runs and isinstance(runs[0], dict) else {}
    db = run.get("db", {}) if isinstance(run, dict) else {}
    table_counts = db.get("table_counts", {}) if isinstance(db, dict) else {}
    counters = artifact.get("counters", {})
    headers = ["Check", "Value"]
    rows = [
        ["Run status", _escape_latex(str(run.get("status", "")))],
        ["Scenario", _escape_latex(str(run.get("scenario_id", "")))],
        ["TLS sessions", str(table_counts.get("tls_sessions", ""))],
        ["Process events", str(table_counts.get("process_events", ""))],
        ["Actions", str(table_counts.get("actions", ""))],
        ["Persistence lost count", str(counters.get("persistence_lost_count", ""))],
        ["TLS drop count", str(counters.get("tls_drop_count", ""))],
        ["Process drop count", str(counters.get("process_drop_count", ""))],
    ]
    return _table(
        headers,
        rows,
        "Backend capture-health canary for collector startup, shutdown, persistence, counters, and core SQLite tables.",
        "tab:backend_capture_health",
        notes="This is a benign canary health gate, not a proof of live capture health for all 67 frozen scenarios.",
    )


def table_probe(perf):
    return _performance_table_if_completed("probe_latency")


def table_throughput(perf):
    return _performance_table_if_completed("throughput")


def table_memory(perf):
    return _performance_table_if_completed("memory")


def table_time_to_detect(perf=None):
    return _performance_table_if_completed("time_to_detect")


def generate_all() -> int:
    report = load_paper_report_v1(RESULTS / "evaluation_report.json")
    tables = [
        table_main(report),
        table_mcnemar(report),
        table_ablation(report),
        table_backend_health(report),
        table_probe(None),
        table_throughput(None),
        table_memory(None),
        table_time_to_detect(None),
    ]
    skipped = sorted(unsupported_claims(report))
    note = "\n".join(["% Skipped unsupported/not-completed sections:"] + [f"% - {item}" for item in skipped])
    output = RESULTS / "paper_tables.tex"
    output.write_text(note + "\n\n" + "\n\n".join(t for t in tables if t) + "\n", encoding="utf-8")
    (RESULTS / "table_preamble.tex").write_text(LATEX_PREAMBLE + "\n", encoding="utf-8")
    print(f"Generated {output}")
    return len([t for t in tables if t])


def _performance_table_if_completed(experiment: str) -> str:
    try:
        artifact = load_completed_artifact(
            PERF_DIR / "performance_results.json",
            expected_schema="mtagentlens.performance_results.v1",
        )
    except PaperReportError:
        return ""
    experiments = artifact.get("experiments", {})
    if not isinstance(experiments, dict) or experiment not in experiments:
        return ""
    result = experiments.get(experiment)
    if not isinstance(result, dict) or result.get("status") != "completed":
        return ""
    if experiment == "probe_latency":
        return _table_probe_latency(result)
    if experiment == "throughput":
        return _table_throughput(result)
    if experiment == "memory":
        return _table_memory(result)
    if experiment == "time_to_detect":
        return _table_time_to_detect(result)
    return ""


def _table_probe_latency(result: dict[str, Any]) -> str:
    rows = []
    for name, summary in result.get("summary", {}).items():
        if not isinstance(summary, dict):
            continue
        rows.append([
            _escape_latex(name),
            str(summary.get("iterations", "")),
            _fmt4(_mean(summary, "baseline_s")),
            _fmt4(_mean(summary, "instrumented_s")),
            _fmt1(_mean(summary, "overhead_per_iteration_ns")),
            _fmt1(_p95(summary, "overhead_per_iteration_ns")),
        ])
    return _table(
        ["Target", "Iterations", "Baseline s", "Instrumented s", "Mean overhead ns", "P95 overhead ns"],
        rows,
        "Local process/file collector overhead microbenchmarks.",
        "tab:performance_probe_latency",
        notes=str(result.get("scope", "")),
    )


def _table_throughput(result: dict[str, Any]) -> str:
    rows = []
    for event_pairs, summary in sorted(result.get("summary", {}).items(), key=lambda item: int(item[0])):
        if not isinstance(summary, dict):
            continue
        rows.append([
            _escape_latex(str(event_pairs)),
            _fmt1(_mean(summary, "full_pipeline_events_per_sec")),
            _fmt1(_mean(summary, "passthrough_events_per_sec")),
            _fmt2(_mean(summary, "analyzer_overhead_pct")),
            _fmt2(_p95(summary, "analyzer_overhead_pct")),
        ])
    return _table(
        ["Event pairs", "Full pipeline ev/s", "Passthrough ev/s", "Mean overhead \\%", "P95 overhead \\%"],
        rows,
        "Synthetic FakeRunner analyzer throughput microbenchmark.",
        "tab:performance_throughput",
        notes=str(result.get("scope", "")),
    )


def _table_memory(result: dict[str, Any]) -> str:
    summary = result.get("summary", {})
    rss = summary.get("rss_mb", {}) if isinstance(summary, dict) else {}
    rows = [
        ["Samples", str(rss.get("n", ""))],
        ["Mean RSS MB", _fmt2(rss.get("mean"))],
        ["Median RSS MB", _fmt2(rss.get("median"))],
        ["P95 RSS MB", _fmt2(rss.get("p95"))],
        ["Peak RSS MB", _fmt2(summary.get("peak_rss_mb"))],
        ["Steady-state RSS MB", _fmt2(summary.get("steady_state_rss_mb"))],
        ["Growth MB/min", _fmt3(summary.get("growth_rate_mb_per_min"))],
    ]
    return _table(
        ["Metric", "Value"],
        rows,
        "Single-host collector RSS under repeated file events.",
        "tab:performance_memory",
        notes=str(result.get("scope", "")),
    )


def _table_time_to_detect(result: dict[str, Any]) -> str:
    lat = result.get("summary", {}).get("latency_ms", {})
    rows = [
        ["Runs", str(lat.get("n", ""))],
        ["Mean latency ms", _fmt2(lat.get("mean"))],
        ["Median latency ms", _fmt2(lat.get("median"))],
        ["P95 latency ms", _fmt2(lat.get("p95"))],
        ["P99 latency ms", _fmt2(lat.get("p99"))],
        ["Min latency ms", _fmt2(lat.get("min"))],
        ["Max latency ms", _fmt2(lat.get("max"))],
    ]
    return _table(
        ["Metric", "Value"],
        rows,
        "First-observation latency for low-level process-runner JSON events.",
        "tab:performance_time_to_detect",
        notes=str(result.get("scope", "")),
    )


def _table(headers, rows, caption, label, notes=""):
    body = [
        "\\begin{table}[t]",
        "  \\centering",
        "  \\begin{threeparttable}",
        f"    \\caption{{{caption}}}",
        f"    \\label{{{label}}}",
        f"    \\begin{{tabular}}{{{'l' + 'c' * (len(headers) - 1)}}}",
        "      \\toprule",
        "      " + " & ".join(f"\\textbf{{{h}}}" for h in headers) + " \\\\",
        "      \\midrule",
    ]
    for row in rows:
        body.append("      " + " & ".join(row) + " \\\\")
    body.extend(["      \\bottomrule", "    \\end{tabular}"])
    if notes:
        body.extend(["    \\begin{tablenotes}", f"      \\footnotesize\\item {notes}", "    \\end{tablenotes}"])
    body.extend(["  \\end{threeparttable}", "\\end{table}"])
    return "\n".join(body)


def _mean(summary: dict[str, Any], key: str) -> Any:
    value = summary.get(key, {})
    return value.get("mean") if isinstance(value, dict) else None


def _p95(summary: dict[str, Any], key: str) -> Any:
    value = summary.get(key, {})
    return value.get("p95") if isinstance(value, dict) else None


def _fmt1(value: Any) -> str:
    return _fmt(value, 1)


def _fmt2(value: Any) -> str:
    return _fmt(value, 2)


def _fmt3(value: Any) -> str:
    return _fmt(value, 3)


def _fmt4(value: Any) -> str:
    return _fmt(value, 4)


def _fmt(value: Any, digits: int) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def _escape_latex(value: str) -> str:
    replacements = {
        "\\": "\\textbackslash{}",
        "&": "\\&",
        "%": "\\%",
        "$": "\\$",
        "#": "\\#",
        "_": "\\_",
        "{": "\\{",
        "}": "\\}",
        "~": "\\textasciitilde{}",
        "^": "\\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in value)


if __name__ == "__main__":
    raise SystemExit(0 if generate_all() else 1)
