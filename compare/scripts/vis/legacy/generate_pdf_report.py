#!/usr/bin/env python3
"""Generate a conservative PDF from completed paper evaluation artifacts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from compare.scripts.vis.paper_report import (  # noqa: E402
    dataset_counts,
    load_paper_report_v1,
    prompt_mcnemar_rows,
    unsupported_claims,
)


PROMPT_TOOL_ORDER = ["mtagentprism", "llm-guard", "meta-prompt-guard", "vigil"]
TOOL_NAMES = {
    "mtagentprism": "MtAgentPrism",
    "llm-guard": "LLM Guard",
    "meta-prompt-guard": "Meta Prompt Guard",
    "vigil": "Vigil",
}


def build_main_table(report: dict) -> tuple[list[str], list[list[str]]]:
    metrics = report["metrics_by_tool"]
    cols = ["Tool", "Precision", "Recall", "F1", "Scenario Recall", "Claim Coverage"]
    rows: list[list[str]] = []
    for tool_id in PROMPT_TOOL_ORDER:
        if tool_id not in metrics:
            continue
        m = metrics[tool_id]
        rows.append(
            [
                TOOL_NAMES.get(tool_id, tool_id),
                _pct(m.get("precision", 0.0)),
                _pct(m.get("recall", 0.0)),
                _pct(m.get("f1", 0.0)),
                _pct(m.get("scenario_recall", m.get("recall", 0.0))),
                _pct(m.get("claim_coverage", 0.0)),
            ]
        )
    return cols, rows


def build_mcnemar_table(report: dict) -> tuple[list[str], list[list[str]]]:
    cols = ["Comparison", "b: MTAP correct / baseline wrong", "c: baseline correct / MTAP wrong", "Yates statistic", "p-value"]
    rows = []
    for row in prompt_mcnemar_rows(report):
        rows.append(
            [
                f"MtAgentPrism vs {TOOL_NAMES.get(row['tool_id'], row['tool_id'])}",
                str(row["b"]),
                str(row["c"]),
                f"{row['statistic']:.2f}",
                f"{row['p_value']:.3e}",
            ]
        )
    return cols, rows


def generate_pdf(results_dir: Path, output_path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    report = load_paper_report_v1(results_dir / "evaluation_report.json")
    total, attack, benign = dataset_counts(report)
    unsupported = sorted(unsupported_claims(report))

    with PdfPages(output_path) as pdf:
        fig, (ax_title, ax_table, ax_mcnemar, ax_scope) = plt.subplots(
            4,
            1,
            figsize=(11, 8.5),
            gridspec_kw={"height_ratios": [0.8, 2.4, 1.6, 1.2]},
        )
        fig.suptitle("MtAgentPrism Paper Evaluation Report", fontsize=14, fontweight="bold", y=0.98)

        ax_title.axis("off")
        ax_title.text(
            0.5,
            0.55,
            f"Frozen dataset v1.0.0: {total} scenarios ({attack} attack, {benign} benign). "
            "Scope: completed frozen MtAgentPrism evaluation and prompt baselines only.",
            ha="center",
            va="center",
            fontsize=9,
            color="#444444",
            transform=ax_title.transAxes,
        )

        cols, rows = build_main_table(report)
        _render_table(ax_table, cols, rows, "Table 1. Completed Main Metrics")

        m_cols, m_rows = build_mcnemar_table(report)
        _render_table(ax_mcnemar, m_cols, m_rows, "Table 2. Scenario-Level Paired McNemar Tests")

        ax_scope.axis("off")
        scope_lines = [
            "Unsupported or not-yet-completed sections are intentionally excluded from this PDF:",
            *[f"- {claim}" for claim in unsupported],
        ]
        ax_scope.text(
            0.01,
            0.95,
            "\n".join(scope_lines),
            ha="left",
            va="top",
            fontsize=8,
            color="#555555",
            transform=ax_scope.transAxes,
        )

        plt.tight_layout(pad=2)
        pdf.savefig(fig, dpi=150)
        plt.close(fig)

        info = pdf.infodict()
        info["Title"] = "MtAgentPrism Paper Evaluation Report"
        info["Author"] = "MtAgentPrism Benchmark"
        info["Subject"] = "Completed frozen evaluation and prompt-baseline comparison"


def _render_table(ax, cols: list[str], rows: list[list[str]], title: str) -> None:
    ax.axis("off")
    ax.set_title(title, fontsize=11, fontweight="bold", pad=8, loc="left")
    table = ax.table(cellText=rows, colLabels=cols, cellLoc="center", loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1.0, 1.3)
    for (row_idx, _col_idx), cell in table.get_celld().items():
        cell.set_edgecolor("#cccccc")
        cell.set_linewidth(0.3)
        if row_idx == 0:
            cell.set_facecolor("#2c3e50")
            cell.set_text_props(color="white", fontweight="bold")
        elif row_idx == 1:
            cell.set_facecolor("#e8f5e9")
            cell.set_text_props(fontweight="bold")
        elif row_idx % 2 == 0:
            cell.set_facecolor("#f8f9fa")


def _pct(value: object) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "n/a"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="compare/results/paper", help="Path to paper results directory")
    parser.add_argument("--output", default="compare/results/paper/evaluation_report.pdf", help="Output PDF path")
    args = parser.parse_args()

    results_dir = Path(args.results).resolve()
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    generate_pdf(results_dir, output_path)
    print(f"PDF report generated: {output_path} ({output_path.stat().st_size // 1024}KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
