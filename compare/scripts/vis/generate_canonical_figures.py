#!/usr/bin/env python3
"""Generate canonical paper figures from completed MtAgentLens artifacts.

This script intentionally avoids matplotlib because the current system
matplotlib build is incompatible with the installed NumPy ABI. It writes SVG
directly and uses cairosvg only for PDF/PNG export.
"""

from __future__ import annotations

import hashlib
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parents[3]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from compare.scripts.vis.paper_report import load_completed_artifact, load_paper_report_v1  # noqa: E402

RESULTS = PROJECT / "compare" / "results" / "paper"
FIG_DIR = RESULTS / "figures"
SOURCE_DIR = RESULTS / "source_data"
PERF_DIR = PROJECT / "compare" / "results" / "perf"
BACKEND_DIR = PROJECT / "compare" / "results" / "backend_health"

PALETTE = {
    "ink": "#24313f",
    "muted": "#64748b",
    "grid": "#d8dee8",
    "mtap": "#315f9f",
    "baseline": "#9aa9bb",
    "accent": "#2f8f83",
    "warning": "#c77d2b",
    "danger": "#b84a4a",
    "pale": "#eef3f8",
}


def main() -> int:
    FIG_DIR.mkdir(parents=True, exist_ok=True)
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)

    report_path = RESULTS / "evaluation_report.json"
    ablation_path = RESULTS / "controlled_ablation_results.json"
    backend_path = BACKEND_DIR / "capture_health.json"
    performance_path = PERF_DIR / "performance_results.json"

    report = load_paper_report_v1(report_path)
    ablation = load_completed_artifact(
        ablation_path, expected_schema="mtagentlens.controlled_ablation_results.v1"
    )
    backend = load_completed_artifact(
        backend_path, expected_schema="mtagentlens.backend_capture_health.v1"
    )
    performance = load_completed_artifact(
        performance_path, expected_schema="mtagentlens.performance_results.v1"
    )

    figures = [
        _fig_main_metrics(report),
        _fig_ablation(ablation),
        _fig_backend_health(backend),
        _fig_performance(performance),
    ]

    manifest = {
        "schema": "mtagentlens.paper_figure_manifest.v1",
        "status": "completed",
        "generated_at": _now_iso(),
        "source_artifacts": {
            "evaluation_report": _artifact_ref(report_path),
            "controlled_ablation": _artifact_ref(ablation_path),
            "backend_capture_health": _artifact_ref(backend_path),
            "performance": _artifact_ref(performance_path),
        },
        "figures": [],
    }

    for figure in figures:
        source_path = SOURCE_DIR / f"{figure['id']}_source_data.json"
        source_path.write_text(
            json.dumps(figure["source_data"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        svg_path = FIG_DIR / f"{figure['id']}.svg"
        svg_path.write_text(figure["svg"], encoding="utf-8")
        exports = {"svg": _display(svg_path), "source_data": _display(source_path)}
        try:
            import cairosvg  # type: ignore

            pdf_path = FIG_DIR / f"{figure['id']}.pdf"
            png_path = FIG_DIR / f"{figure['id']}.png"
            cairosvg.svg2pdf(bytestring=figure["svg"].encode("utf-8"), write_to=str(pdf_path))
            cairosvg.svg2png(bytestring=figure["svg"].encode("utf-8"), write_to=str(png_path), dpi=300)
            exports["pdf"] = _display(pdf_path)
            exports["png"] = _display(png_path)
        except Exception as exc:  # noqa: BLE001 - manifest records export availability.
            exports["raster_export_error"] = f"{type(exc).__name__}: {exc}"
        manifest["figures"].append(
            {
                "id": figure["id"],
                "title": figure["title"],
                "claim": figure["claim"],
                "panels": figure["panels"],
                "outputs": exports,
            }
        )

    manifest_path = FIG_DIR / "figure_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Generated {_display(manifest_path)}")
    for item in manifest["figures"]:
        print(f"Generated {item['id']}: {item['outputs']}")
    return 0


def _fig_main_metrics(report: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for tool_id in ("mtagentlens", "llm-guard", "meta-prompt-guard", "vigil"):
        metric = report["metrics_by_tool"][tool_id]
        rows.append(
            {
                "label": _tool_name(tool_id),
                "tool_id": tool_id,
                "f1": float(metric.get("f1", 0.0)),
                "scenario_recall": float(metric.get("scenario_recall", 0.0)),
            }
        )
    svg = _svg_page(
        "Figure 1. Frozen 67-scenario evaluation",
        "MtAgentLens preserves scenario recall where prompt baselines do not.",
        [_grouped_bar_panel(70, 95, 980, 430, rows, ["f1", "scenario_recall"])],
        width=1120,
        height=620,
    )
    return {
        "id": "fig1_main_metrics",
        "title": "Frozen 67-scenario evaluation",
        "claim": "MtAgentLens achieves full frozen-set recall under the completed prompt-input contract.",
        "panels": ["F1 and scenario recall for MtAgentLens and prompt baselines."],
        "svg": svg,
        "source_data": {"schema": "mtagentlens.figure_source_data.v1", "rows": rows},
    }


def _fig_ablation(ablation: dict[str, Any]) -> dict[str, Any]:
    rows = [
        {
            "label": "Full replay",
            "scenario_recall": float(ablation["baseline"]["metrics"]["scenario_recall"]),
        }
    ]
    for variant in ablation.get("variants", []):
        if isinstance(variant, dict) and variant.get("status") == "completed":
            rows.append(
                {
                    "label": str(variant.get("label") or variant.get("variant_id")),
                    "scenario_recall": float(variant["metrics"]["scenario_recall"]),
                }
            )
    svg = _svg_page(
        "Figure 2. Controlled persisted-graph ablation",
        "File-read policy-boundary signals drive the main persisted-graph recall drop.",
        [_horizontal_bar_panel(70, 95, 980, 470, rows, "scenario_recall")],
        width=1120,
        height=660,
    )
    return {
        "id": "fig2_controlled_ablation",
        "title": "Controlled persisted-graph ablation",
        "claim": "Within persisted SQLite replay, disabling file-read action-family signals reduces scenario recall from 1.000 to 0.537.",
        "panels": ["Scenario recall for full replay and each completed action-family feature-flag variant."],
        "svg": svg,
        "source_data": {
            "schema": "mtagentlens.figure_source_data.v1",
            "methodology": ablation.get("methodology"),
            "rows": rows,
            "claim_boundary": ablation.get("claim_scope", {}),
        },
    }


def _fig_backend_health(backend: dict[str, Any]) -> dict[str, Any]:
    counters = backend.get("counters", {})
    run = backend.get("runs", [{}])[0]
    db_counts = run.get("db", {}).get("table_counts", {}) if isinstance(run, dict) else {}
    backend_rows = [
        {"label": "persistence lost", "value": float(counters.get("persistence_lost_count", 0))},
        {"label": "TLS drops", "value": float(counters.get("tls_drop_count", 0))},
        {"label": "process drops", "value": float(counters.get("process_drop_count", 0))},
        {"label": "TLS sessions", "value": float(db_counts.get("tls_sessions", 0))},
        {"label": "process events", "value": float(db_counts.get("process_events", 0))},
        {"label": "actions", "value": float(db_counts.get("actions", 0))},
    ]
    svg = _svg_page(
        "Figure 3. Backend capture-health canary",
        "The backend canary has zero loss/drop counters and confirms core SQLite persistence.",
        [
            _backend_panel(320, 100, 480, 400, backend_rows),
        ],
        width=1120,
        height=610,
    )
    return {
        "id": "fig3_backend_health",
        "title": "Backend capture-health canary",
        "claim": "The backend canary records zero loss/drop counters and confirms core SQLite persistence.",
        "panels": [
            "Backend canary counters and required non-empty SQLite table counts.",
        ],
        "svg": svg,
        "source_data": {
            "schema": "mtagentlens.figure_source_data.v1",
            "backend_rows": backend_rows,
            "claim_boundary": backend.get("claim_scope", {}),
        },
    }


def _fig_performance(performance: dict[str, Any]) -> dict[str, Any]:
    experiments = performance["experiments"]
    probe_rows = [
        {"label": key, "value": _stat(summary, "overhead_per_iteration_ns", "mean")}
        for key, summary in experiments["probe_latency"]["summary"].items()
    ]
    throughput_rows = [
        {"label": f"{key} pairs", "value": _stat(summary, "full_pipeline_events_per_sec", "mean")}
        for key, summary in sorted(experiments["throughput"]["summary"].items(), key=lambda item: int(item[0]))
    ]
    mem = experiments["memory"]["summary"]
    ttd = experiments["time_to_detect"]["summary"]["latency_ms"]
    metric_rows = [
        {"label": "Peak RSS MB", "value": float(mem.get("peak_rss_mb", 0))},
        {"label": "TTD mean ms", "value": float(ttd.get("mean", 0))},
        {"label": "TTD p95 ms", "value": float(ttd.get("p95", 0))},
    ]
    svg = _svg_page(
        "Figure 4. Local performance microbenchmarks",
        "Performance claims are limited to local process/file microbenchmarks and low-level runner visibility.",
        [
            _simple_bar_panel(70, 110, 300, 360, probe_rows, max_value=None, suffix=" ns"),
            _simple_bar_panel(410, 110, 300, 360, throughput_rows, max_value=None, suffix=" ev/s"),
            _simple_bar_panel(750, 110, 300, 360, metric_rows, max_value=None, suffix=""),
        ],
        width=1120,
        height=600,
    )
    return {
        "id": "fig4_performance",
        "title": "Local performance microbenchmarks",
        "claim": "The completed performance artifact supports local overhead, throughput, RSS, and low-level process-runner first-observation latency only.",
        "panels": [
            "Mean per-iteration process/file overhead.",
            "Synthetic analyzer throughput.",
            "Peak RSS and low-level process-runner latency.",
        ],
        "svg": svg,
        "source_data": {
            "schema": "mtagentlens.figure_source_data.v1",
            "probe_rows": probe_rows,
            "throughput_rows": throughput_rows,
            "metric_rows": metric_rows,
            "claim_boundary": performance.get("claim_boundary", {}),
        },
    }


def _svg_page(title: str, subtitle: str, panels: list[str], *, width: int, height: int) -> str:
    body = [
        f'<rect width="{width}" height="{height}" fill="white"/>',
        f'<text x="56" y="48" font-size="24" font-weight="700" fill="{PALETTE["ink"]}">{_esc(title)}</text>',
        f'<text x="56" y="78" font-size="13" fill="{PALETTE["muted"]}">{_esc(subtitle)}</text>',
        *panels,
    ]
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<style>text{font-family:Arial,Helvetica,sans-serif}.small{font-size:11px}.axis{font-size:12px}</style>',
            *body,
            "</svg>",
        ]
    )


def _grouped_bar_panel(x: int, y: int, w: int, h: int, rows: list[dict[str, Any]], keys: list[str]) -> str:
    max_value = 1.0
    plot_h = h - 90
    group_w = w / len(rows)
    bar_w = min(34, group_w / (len(keys) + 1))
    parts = [_axis_frame(x, y, w, plot_h, max_value)]
    colors = [PALETTE["mtap"], PALETTE["accent"], PALETTE["warning"]]
    for i, row in enumerate(rows):
        base_x = x + i * group_w + group_w * 0.22
        for j, key in enumerate(keys):
            value = float(row[key])
            bh = plot_h * value / max_value
            bx = base_x + j * (bar_w + 5)
            by = y + plot_h - bh
            parts.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" fill="{colors[j]}"/>')
            parts.append(f'<text x="{bx + bar_w/2:.1f}" y="{by - 5:.1f}" text-anchor="middle" class="small" fill="{PALETTE["ink"]}">{value:.2f}</text>')
        parts.append(_wrapped_label(row["label"], x + i * group_w + group_w / 2, y + plot_h + 28, 95))
    parts.extend(_legend(x + 25, y + plot_h + 62, [("F1", colors[0]), ("Scenario recall", colors[1]), ("Claim coverage", colors[2])]))
    return "\n".join(parts)


def _horizontal_bar_panel(x: int, y: int, w: int, h: int, rows: list[dict[str, Any]], key: str) -> str:
    bar_h = 24
    gap = 13
    label_w = 360
    plot_w = w - label_w - 50
    parts = []
    for i, row in enumerate(rows):
        yy = y + i * (bar_h + gap)
        value = float(row[key])
        color = PALETTE["mtap"] if i == 0 else (PALETTE["danger"] if value < 0.8 else PALETTE["accent"])
        parts.append(_wrapped_label(row["label"], x, yy + 17, label_w - 18, anchor="start"))
        parts.append(f'<rect x="{x + label_w}" y="{yy}" width="{plot_w}" height="{bar_h}" fill="{PALETTE["pale"]}"/>')
        parts.append(f'<rect x="{x + label_w}" y="{yy}" width="{plot_w * value:.1f}" height="{bar_h}" fill="{color}"/>')
        parts.append(f'<text x="{x + label_w + plot_w + 12}" y="{yy + 17}" class="axis" fill="{PALETTE["ink"]}">{value:.3f}</text>')
    parts.append(f'<text x="{x + label_w}" y="{y + h - 18}" class="small" fill="{PALETTE["muted"]}">Claim coverage, persisted-graph replay scope</text>')
    return "\n".join(parts)


def _simple_bar_panel(
    x: int,
    y: int,
    w: int,
    h: int,
    rows: list[dict[str, Any]],
    *,
    max_value: float | None,
    suffix: str,
) -> str:
    if not rows:
        return ""
    values = [abs(float(row["value"])) for row in rows]
    scale_max = max_value if max_value is not None else max(values) * 1.15 or 1.0
    plot_h = h - 70
    bar_w = min(58, (w - 30) / max(len(rows), 1) * 0.55)
    group_w = (w - 30) / max(len(rows), 1)
    parts = [_axis_frame(x, y, w, plot_h, scale_max)]
    zero_y = y + plot_h if min(float(row["value"]) for row in rows) >= 0 else y + plot_h / 2
    for i, row in enumerate(rows):
        value = float(row["value"])
        bh = abs(value) / scale_max * plot_h
        bx = x + 20 + i * group_w + (group_w - bar_w) / 2
        by = zero_y - bh if value >= 0 else zero_y
        color = PALETTE["mtap"] if value >= 0 else PALETTE["danger"]
        parts.append(f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" fill="{color}"/>')
        parts.append(f'<text x="{bx + bar_w/2:.1f}" y="{by - 6 if value >= 0 else by + bh + 14:.1f}" text-anchor="middle" class="small" fill="{PALETTE["ink"]}">{_format_value(value)}{_esc(suffix)}</text>')
        parts.append(_wrapped_label(str(row["label"]), bx + bar_w / 2, y + plot_h + 25, group_w, anchor="middle"))
    return "\n".join(parts)


def _backend_panel(x: int, y: int, w: int, h: int, rows: list[dict[str, Any]]) -> str:
    parts = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{PALETTE["pale"]}" stroke="{PALETTE["grid"]}"/>']
    parts.append(f'<text x="{x + 20}" y="{y + 32}" font-size="16" font-weight="700" fill="{PALETTE["ink"]}">Backend canary</text>')
    yy = y + 64
    for row in rows:
        value = float(row["value"])
        color = PALETTE["accent"] if value == 0 else PALETTE["mtap"]
        parts.append(f'<text x="{x + 24}" y="{yy}" class="axis" fill="{PALETTE["ink"]}">{_esc(row["label"])}</text>')
        parts.append(f'<text x="{x + w - 26}" y="{yy}" text-anchor="end" font-size="15" font-weight="700" fill="{color}">{_format_value(value)}</text>')
        yy += 45
    return "\n".join(parts)


def _axis_frame(x: int, y: int, w: int, h: int, max_value: float) -> str:
    parts = [f'<line x1="{x}" y1="{y + h}" x2="{x + w}" y2="{y + h}" stroke="{PALETTE["ink"]}" stroke-width="1"/>']
    for frac in (0.25, 0.5, 0.75, 1.0):
        yy = y + h - h * frac
        parts.append(f'<line x1="{x}" y1="{yy:.1f}" x2="{x + w}" y2="{yy:.1f}" stroke="{PALETTE["grid"]}" stroke-width="1"/>')
        parts.append(f'<text x="{x - 8}" y="{yy + 4:.1f}" text-anchor="end" class="small" fill="{PALETTE["muted"]}">{_format_value(max_value * frac)}</text>')
    return "\n".join(parts)


def _legend(x: int, y: int, items: list[tuple[str, str]]) -> list[str]:
    parts = []
    xx = x
    for label, color in items:
        parts.append(f'<rect x="{xx}" y="{y - 10}" width="12" height="12" fill="{color}"/>')
        parts.append(f'<text x="{xx + 18}" y="{y}" class="small" fill="{PALETTE["ink"]}">{_esc(label)}</text>')
        xx += 145
    return parts


def _wrapped_label(label: str, x: float, y: float, width: float, anchor: str = "middle") -> str:
    words = label.replace("-", " ").replace("_", " ").split()
    lines: list[str] = []
    current = ""
    max_chars = max(8, int(width / 7.2))
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    lines = lines[:3]
    dx = 0
    text_anchor = anchor
    tspans = []
    for idx, line in enumerate(lines):
        tspans.append(f'<tspan x="{x:.1f}" dx="{dx}" dy="{0 if idx == 0 else 13}">{_esc(line)}</tspan>')
    return f'<text text-anchor="{text_anchor}" x="{x:.1f}" y="{y:.1f}" class="small" fill="{PALETTE["muted"]}">' + "".join(tspans) + "</text>"


def _stat(summary: dict[str, Any], key: str, stat: str) -> float:
    value = summary.get(key, {})
    if isinstance(value, dict):
        return float(value.get(stat, 0.0))
    return 0.0


def _format_value(value: float) -> str:
    if abs(value) >= 100:
        return f"{value:.0f}"
    if abs(value) >= 10:
        return f"{value:.1f}"
    return f"{value:.2f}"


def _tool_name(tool_id: str) -> str:
    return {
        "mtagentlens": "MtAgentLens",
        "llm-guard": "LLM Guard",
        "meta-prompt-guard": "Meta Prompt Guard",
        "vigil": "Vigil",
    }.get(tool_id, tool_id)


def _artifact_ref(path: Path) -> dict[str, str]:
    return {"path": _display(path), "sha256": _sha256(path)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _display(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT))
    except ValueError:
        return str(resolved)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _esc(value: str) -> str:
    return html.escape(value, quote=True)


if __name__ == "__main__":
    raise SystemExit(main())
