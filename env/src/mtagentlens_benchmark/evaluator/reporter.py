"""Report generation — Markdown, HTML, JSON, LaTeX."""
from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime

from mtagentlens_benchmark.types import BenchmarkScore


def generate_report(score: BenchmarkScore, output_dir: Path, fmt: str = "markdown") -> Path:
    """Generate an evaluation report in the specified format."""
    output_dir.mkdir(parents=True, exist_ok=True)

    generators = {
        "json": _generate_json,
        "markdown": _generate_markdown,
        "html": _generate_html,
        "latex": _generate_latex,
    }
    if fmt not in generators:
        raise ValueError(f"Unknown format: {fmt}")
    return generators[fmt](score, output_dir)


def _generate_json(score: BenchmarkScore, output_dir: Path) -> Path:
    path = output_dir / "evaluation_report.json"
    data = {
        "overall_score": round(score.overall, 2),
        "detection": {
            "precision": score.detection.precision,
            "recall": score.detection.recall,
            "f1": score.detection.f1,
            "tp": score.detection.tp,
            "fp": score.detection.fp,
            "fn": score.detection.fn,
        },
        "causal_chain": {
            "chain_completeness": score.causal_chain.chain_completeness,
            "link_precision": score.causal_chain.link_precision,
            "link_recall": score.causal_chain.link_recall,
            "broken_chains": score.causal_chain.broken_chains,
        },
        "identity": {
            "process_mapping_accuracy": score.identity.process_mapping_accuracy,
        },
        "false_positive": {
            "baseline_fp_rate": score.false_positive.baseline_fp_rate,
            "baseline_assessed": score.false_positive.baseline_assessed,
            "per_baseline_breakdown": score.false_positive.per_baseline_breakdown,
        },
        "robustness": {
            "signal_contribution": score.robustness.signal_contribution,
        },
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _generate_markdown(score: BenchmarkScore, output_dir: Path) -> Path:
    path = output_dir / "evaluation_report.md"
    lines = [
        "# MtAgentLens Benchmark Evaluation Report",
        "",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Overall Score:** {score.overall:.2f}/100",
        "",
        "## Layer 1: Detection (P/R/F1) — Weight 35%",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Precision | {score.detection.precision:.4f} |",
        f"| Recall | {score.detection.recall:.4f} |",
        f"| F1 | {score.detection.f1:.4f} |",
        f"| True Positives | {score.detection.tp} |",
        f"| False Positives | {score.detection.fp} |",
        f"| False Negatives | {score.detection.fn} |",
        "",
        "## Layer 2: Causal Chain Completeness — Weight 20%",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Chain Completeness | {score.causal_chain.chain_completeness:.4f} |",
        f"| Link Precision | {score.causal_chain.link_precision:.4f} |",
        f"| Link Recall | {score.causal_chain.link_recall:.4f} |",
        f"| Broken Chains | {score.causal_chain.broken_chains} |",
        "",
        "## Layer 3: Identity Resolution — Weight 15%",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Process Mapping Accuracy | {score.identity.process_mapping_accuracy:.4f} |",
        "",
        "## Layer 4: False Positive Control — Weight 15%",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Baseline FP Rate | {score.false_positive.baseline_fp_rate:.4f}"
        + ("" if score.false_positive.baseline_assessed else " (NOT ASSESSED — no baseline ran)")
        + " |",
    ]
    for baseline, count in score.false_positive.per_baseline_breakdown.items():
        lines.append(f"| {baseline} | {count} |")

    lines += [
        "",
        "## Layer 5: Robustness — Weight 15%",
        "",
        "| Signal | Contribution |",
        "|--------|-------------|",
    ]
    for signal, contrib in score.robustness.signal_contribution.items():
        lines.append(f"| {signal} | {contrib:.4f} |")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _generate_html(score: BenchmarkScore, output_dir: Path) -> Path:
    md_path = _generate_markdown(score, output_dir)
    html_path = output_dir / "evaluation_report.html"
    html_path.write_text(
        f"<html><body><pre>{md_path.read_text()}</pre></body></html>",
        encoding="utf-8",
    )
    return html_path


def _generate_latex(score: BenchmarkScore, output_dir: Path) -> Path:
    path = output_dir / "evaluation_report.tex"
    latex = rf"""\documentclass{{article}}
\begin{{document}}
\title{{MtAgentLens Benchmark Evaluation}}
\date{{{datetime.now().strftime('%Y-%m-%d')}}}
\maketitle
\section{{Overall Score: {score.overall:.2f}/100}}
\subsection{{Detection (35\%)}}
P={{{score.detection.precision:.3f}}}, R={{{score.detection.recall:.3f}}}, F1={{{score.detection.f1:.3f}}}
\subsection{{Causal Chain (20\%)}}
Completeness={{{score.causal_chain.chain_completeness:.3f}}}
\subsection{{Identity (15\%)}}
Mapping Accuracy={{{score.identity.process_mapping_accuracy:.3f}}}
\subsection{{False Positive (15\%)}}
Baseline FP Rate={{{score.false_positive.baseline_fp_rate:.3f}}}
Baseline Assessed={{{score.false_positive.baseline_assessed}}}
\end{{document}}
"""
    path.write_text(latex, encoding="utf-8")
    return path
