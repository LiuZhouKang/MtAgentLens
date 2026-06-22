#!/usr/bin/env python3
"""将 Nature 期刊级 LaTeX 表格渲染为 PDF 图片。

每张表独立编译, 输出 PDF 矢量图片 (TrueType 嵌入, 可搜索中文文本)。

用法: python3 compare/scripts/render_tables.py
"""

from __future__ import annotations

import json, subprocess, tempfile, shutil
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]
RESULTS = PROJECT / "compare/results/paper"
PERF_DIR = PROJECT / "compare/results/perf"
FIG_DIR = RESULTS / "figures"

# ── 从 nature_tables 导入 builder 函数 ──
import sys
sys.path.insert(0, str(PROJECT))
from compare.scripts.vis.nature_tables import (
    table_main, table_heatmap, table_mcnemar, table_ablation,
    table_probe, table_throughput, table_memory,
)

PREAMBLE = r"""
\documentclass[12pt,twoside]{article}
\usepackage[paperwidth=210mm,paperheight=297mm,left=12mm,right=12mm,top=8mm,bottom=8mm]{geometry}
\usepackage{ctex}
\setCJKmainfont{AR PL UMing CN}
\setCJKsansfont{AR PL UMing CN}
\usepackage{booktabs}
\usepackage[table]{xcolor}
\usepackage{threeparttable}
\usepackage{siunitx}
\usepackage{multirow}
\usepackage{makecell}
\usepackage{amsmath}
\usepackage{hyperref}

\definecolor{ashero}{HTML}{384878}
\definecolor{asdark}{HTML}{1B3A5C}
\definecolor{asmid}{HTML}{6878A8}
\definecolor{aslight}{HTML}{D0D4E8}
\definecolor{aspale}{HTML}{F0F2F6}
\definecolor{siggreen}{HTML}{2E7D32}
\definecolor{sigred}{HTML}{C62828}
\definecolor{grayrow}{HTML}{F5F5F5}

\newcommand{\asrow}{\rowcolor{aspale}}
\newcommand{\sigrow}[1]{\textcolor{siggreen}{\textbf{#1}}}
\newcommand{\nonsigrow}{{\small \textcolor{gray!60}{---}}}

\pagestyle{empty}
\begin{document}
\centering
\vspace*{4mm}
TABLE_CONTENT
\end{document}
""".strip()


def compile_one(name: str, latex_block: str, tmpdir: Path) -> bool:
    """编译单张表为 PDF."""
    tex = PREAMBLE.replace("TABLE_CONTENT", latex_block)
    tex_file = tmpdir / f"{name}.tex"
    tex_file.write_text(tex)

    for _pass in range(2):
        result = subprocess.run(
            ["xelatex", "-interaction=nonstopmode", "-output-directory", str(tmpdir),
             str(tex_file.name)],
            capture_output=True, text=True, cwd=str(tmpdir), timeout=90
        )
        combined = result.stderr + result.stdout
        if "Fatal error" in combined:
            for line in combined.split("\n"):
                if "Error" in line or line.startswith("! "):
                    print(f"      {line.strip()[:140]}")
            return False
        if "Rerun to get" not in combined:
            break

    pdf = tmpdir / f"{name}.pdf"
    if pdf.exists() and pdf.stat().st_size > 500:
        dst = FIG_DIR / f"{name}.pdf"
        shutil.copy2(pdf, dst)
        return True
    return False


def main():
    print("═" * 55)
    print("  Nature 期刊级表格 → PDF 图片渲染")
    print("═" * 55)

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    report = json.loads((RESULTS / "evaluation_report.json").read_text())
    perf = {}

    builders = [
        ("tab1_main", lambda: table_main(report)),
        ("tab3_mcnemar", lambda: table_mcnemar(report)),
        ("tab5a_probe", lambda: table_probe(perf)),
        ("tab5b_throughput", lambda: table_throughput(perf)),
        ("tab5c_memory", lambda: table_memory(perf)),
    ]

    rendered = 0
    tmpdir = Path(tempfile.mkdtemp(prefix="astex_"))
    print()

    for name, builder in builders:
        block = builder()
        if not block:
            print(f"  ✗ {name}: builder 返回空")
            continue

        ok = compile_one(name, block, tmpdir)
        if ok:
            pdf = FIG_DIR / f"{name}.pdf"
            print(f"  ✓ {name}.pdf ({pdf.stat().st_size/1024:.0f} KB)")
            rendered += 1
        else:
            print(f"  ✗ {name}: 编译失败")

    shutil.rmtree(tmpdir, ignore_errors=True)
    print(f"\n  ✓ {rendered}/{len(builders)} 张表格图片 → {FIG_DIR}/")
    return rendered


if __name__ == "__main__":
    main()
