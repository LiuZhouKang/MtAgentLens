#!/usr/bin/env python3
"""生成当前已完成论文实验的中文 LaTeX 表格。"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from compare.scripts.vis.paper_report import (  # noqa: E402
    dataset_counts,
    load_paper_report_v1,
    prompt_mcnemar_rows,
    unsupported_claims,
)

RESULTS = PROJECT / "compare" / "results" / "paper"
TOOL_ORDER = ["mtagentprism", "llm-guard", "meta-prompt-guard", "vigil"]
TOOL_NAMES = {
    "mtagentprism": "MtAgentPrism",
    "llm-guard": "LLM Guard",
    "meta-prompt-guard": "Meta Prompt Guard",
    "vigil": "Vigil",
}


def latex_table(headers, rows, caption, label, placement="t"):
    n = len(headers)
    return "\n".join([
        f"\\begin{{table}}[{placement}]",
        "  \\centering",
        f"  \\caption{{{caption}}}",
        f"  \\label{{{label}}}",
        f"  \\begin{{tabular}}{{{'l' + 'c' * (n - 1)}}}",
        "    \\toprule",
        f"    {' & '.join(f'\\textbf{{{h}}}' for h in headers)} \\\\",
        "    \\midrule",
    ] + [f"    {' & '.join(str(c) for c in r)} \\\\" for r in rows] + [
        "    \\bottomrule",
        "  \\end{tabular}",
        "\\end{table}",
    ])


def build_main_table(report):
    total, attack, benign = dataset_counts(report)
    metrics = report["metrics_by_tool"]
    headers = ["工具", "精确率", "召回率", "F1", "场景召回率", "声明覆盖率"]
    rows = []
    for tid in TOOL_ORDER:
        if tid not in metrics:
            continue
        m = metrics[tid]
        name = TOOL_NAMES.get(tid, tid)
        bold = tid == "mtagentprism"
        rows.append([
            f"\\textbf{{{name}}}" if bold else name,
            _pct(m.get("precision", 0.0)),
            _pct(m.get("recall", 0.0)),
            _pct(m.get("f1", 0.0)),
            _pct(m.get("scenario_recall", m.get("recall", 0.0))),
            _pct(m.get("claim_coverage", 0.0)),
        ])
    return latex_table(
        headers,
        rows,
        f"当前已完成主实验检测性能对比（$n={total}$，{attack} 攻击 + {benign} 良性）。",
        "tab:main_completed",
    )


def build_mcnemar_table(report):
    headers = ["对比组", "MTAP 正确/基线错误", "基线正确/MTAP 错误", "Yates 统计量", "$p$ 值"]
    rows = []
    for row in prompt_mcnemar_rows(report):
        rows.append([
            f"MtAgentPrism vs {TOOL_NAMES.get(row['tool_id'], row['tool_id'])}",
            str(row["b"]),
            str(row["c"]),
            f"{row['statistic']:.2f}",
            f"{row['p_value']:.3e}",
        ])
    return latex_table(
        headers,
        rows,
        "场景级配对 McNemar 检验。统计范围仅限已完成的冻结数据集与 prompt baseline。",
        "tab:mcnemar_completed",
    )


def build_scope_note(report):
    claims = sorted(unsupported_claims(report))
    lines = [
        "% 以下实验项尚无 completed artifact，未生成正式论文表格：",
        *[f"% - {claim}" for claim in claims],
    ]
    return "\n".join(lines)


def _pct(value):
    return f"{float(value) * 100:.1f}\\%"


def main() -> int:
    report = load_paper_report_v1(RESULTS / "evaluation_report.json")
    tables = [
        build_scope_note(report),
        build_main_table(report),
        build_mcnemar_table(report),
    ]
    output = RESULTS / "paper_tables.tex"
    output.write_text("\n\n".join(tables) + "\n", encoding="utf-8")
    print(f"已生成 {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
