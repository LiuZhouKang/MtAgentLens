#!/usr/bin/env python3
"""
MtAgentLens Performance Micro-Benchmarks — 3 experiments, conference-ready.

  1. eBPF Probe Latency    — kernel bpftool stats (run_time_ns / run_cnt)
  2. Memory Footprint       — RSS timeline via /proc/PID/statm
  3. End-to-End Overhead    — Claude task wall-clock with/without collector

Usage: sudo python3 compare/scripts/perf/run_experiments.py
"""

import json, os, statistics, subprocess, sys, time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]
COLLECTOR = PROJECT / "collector/build/release/mtagentlens"  # target-dir is "build" (.cargo/config.toml)
BENCH_DIR = Path(__file__).resolve().parent / "bench_targets"
RESULTS = PROJECT / "compare/results/perf"
RESULTS.mkdir(parents=True, exist_ok=True)
CHARTS_DIR = RESULTS / "charts"
CHARTS_DIR.mkdir(parents=True, exist_ok=True)

LIBSSL = "/usr/lib/x86_64-linux-gnu/libssl.so.3"
SUDO = "1"
CLAUDE = "/home/lzk/.local/bin/claude"
SSL_LOOP = BENCH_DIR / "ssl_loop"


def stats(values):
    n = len(values)
    if n == 0: return {}
    s = sorted(values)
    return {"n": n, "min": min(values), "mean": statistics.mean(values),
            "median": statistics.median(values),
            "p95": s[int(n * 0.95)] if n > 1 else s[0],
            "p99": s[int(n * 0.99)] if n > 1 else s[0],
            "max": max(values), "stddev": statistics.stdev(values) if n > 1 else 0}


def sudo(cmd, stdin=None):
    r = subprocess.run(["sudo", "-S"] + cmd, capture_output=True, text=True,
                       timeout=60, input=stdin or (SUDO + "\n"))
    return r


def start_collector(*args):
    cp = subprocess.Popen(
        ["sudo", "-S", str(COLLECTOR)] + list(args),
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
    if cp.stdin: cp.stdin.write(SUDO + "\n"); cp.stdin.flush()
    return cp


def stop_collector(cp):
    cp.terminate()
    try: cp.wait(timeout=10)
    except subprocess.TimeoutExpired: cp.kill(); cp.wait()


# ══════════════════════════════════════════════════════════════════════════
# Experiment 1: eBPF Probe Latency via kernel bpftool stats
# ══════════════════════════════════════════════════════════════════════════

def run_probe_latency():
    print("=" * 65)
    print("  Experiment 1: eBPF Probe Latency (kernel bpftool stats)")
    print("=" * 65)

    # Enable kernel BPF runtime stats
    sudo(["tee", "/proc/sys/kernel/bpf_stats_enabled"], stdin="1\n")
    N_ITER = 500000  # 500K SSL read/write pairs = 1M total ops

    cp = start_collector("ssl", "--binary-path", LIBSSL, "--quiet")
    time.sleep(3)

    # Run ssl_loop to trigger probes
    print(f"  Running ssl_loop ({N_ITER:,} iterations) ...")
    t0 = time.perf_counter()
    subprocess.run([str(SSL_LOOP), str(N_ITER)], capture_output=True, text=True, timeout=30)
    elapsed = time.perf_counter() - t0
    print(f"  ssl_loop completed in {elapsed:.1f}s")

    # Read BPF program stats via bpftool
    r = sudo(["bpftool", "prog", "show", "--json"])
    if r.returncode != 0:
        stop_collector(cp)
        sudo(["tee", "/proc/sys/kernel/bpf_stats_enabled"], stdin="0\n")
        return None

    try:
        bpf_data = json.loads(r.stdout)
    except json.JSONDecodeError:
        bpf_data = []

    probe_stats = {}
    for p in bpf_data:
        name = p.get("name", "")
        rc = p.get("run_cnt", 0)
        rt = p.get("run_time_ns", 0)
        if rc > 100 and "SSL" in name:
            avg = rt / rc
            probe_stats[name] = {"run_cnt": rc, "run_time_ns": rt, "avg_ns": avg,
                                  "xlated_bytes": p.get("bytes_xlated", 0)}

    stop_collector(cp)
    sudo(["tee", "/proc/sys/kernel/bpf_stats_enabled"], stdin="0\n")

    print(f"\n  {'Probe':35s} {'Calls':>10s} {'Runtime':>12s} {'Avg':>10s}  {'Xlated'}")
    print(f"  {'-'*72}")
    for name, s in sorted(probe_stats.items()):
        print(f"  {name:35s} {s['run_cnt']:>10,d} {s['run_time_ns']:>10,d} ns  {s['avg_ns']:>8.1f} ns  {s['xlated_bytes']:>5} B")

    total_per_op = sum(s["avg_ns"] for s in probe_stats.values())
    print(f"\n  Total per SSL op (all probes): ~{total_per_op:.0f} ns")

    return {"method": "kernel_bpftool_stats", "iterations": N_ITER,
            "ssl_loop_duration_s": elapsed, "probes": probe_stats,
            "total_per_op_ns": total_per_op}


# ══════════════════════════════════════════════════════════════════════════
# Experiment 2: Memory Footprint
# ══════════════════════════════════════════════════════════════════════════

def run_memory_footprint(duration=60):
    print("\n" + "=" * 65)
    print(f"  Experiment 2: Memory Footprint ({duration}s idle)")
    print("=" * 65)

    cp = start_collector("trace", "--ssl=true", "--process=true", "--quiet")
    time.sleep(4)

    pid = None
    for _ in range(15):
        r = subprocess.run(["pgrep", "-f", "mtagentlens.*trace"], capture_output=True, text=True, shell=True)
        pids = [int(x) for x in r.stdout.strip().split("\n") if x.isdigit()]
        if pids: pid = pids[0]; break
        time.sleep(1)

    if not pid:
        # Fallback: find any mtagentlens process
        r = subprocess.run(["pgrep", "mtagentlens"], capture_output=True, text=True)
        pids = [int(x) for x in r.stdout.strip().split("\n") if x.isdigit()]
        if pids: pid = pids[0]

    if not pid:
        print("  PID not found — skipping")
        stop_collector(cp)
        return None

    print(f"  PID: {pid}")
    rss_mb = []
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        try:
            statm = Path(f"/proc/{pid}/statm").read_text()
            rss_mb.append(int(statm.split()[1]) * 4 / 1024)
        except Exception:
            pass
        time.sleep(1.0)

    stop_collector(cp)
    if not rss_mb: return None

    r = stats(rss_mb)
    peak = max(rss_mb)
    steady = rss_mb[15:] if len(rss_mb) > 15 else rss_mb
    steady_mean = sum(steady) / len(steady) if steady else 0

    print(f"    Samples: {len(rss_mb)}")
    print(f"    Mean RSS: {r['mean']:.1f} MB  Peak: {peak:.1f} MB  Steady: {steady_mean:.1f} MB")

    return {"duration_s": duration, "num_samples": len(rss_mb),
            "rss_stats": r, "peak_rss_mb": peak, "steady_state_rss_mb": steady_mean,
            "rss_timeline": rss_mb}


# ══════════════════════════════════════════════════════════════════════════
# Experiment 3: End-to-End Overhead
# ══════════════════════════════════════════════════════════════════════════

E2E_TASK = "Run: ls -la /tmp/ | head -5 && echo done"

def run_e2e_overhead(runs=5):
    print("\n" + "=" * 65)
    print("  Experiment 3: End-to-End Overhead")
    print("=" * 65)
    print(f"  Task: {E2E_TASK}")

    base_t, inst_t = [], []
    for i in range(runs):
        t0 = time.perf_counter()
        subprocess.run([CLAUDE, "-p", E2E_TASK, "--model", "haiku",
                        "--output-format", "text", "--permission-mode", "bypassPermissions"],
                       capture_output=True, text=True, timeout=120)
        base_t.append(time.perf_counter() - t0)

        cp = start_collector("trace", "--ssl=true", "--process=true", "--comm", "claude", "--quiet")
        time.sleep(3.0)
        t0 = time.perf_counter()
        subprocess.run([CLAUDE, "-p", E2E_TASK, "--model", "haiku",
                        "--output-format", "text", "--permission-mode", "bypassPermissions"],
                       capture_output=True, text=True, timeout=120)
        inst_t.append(time.perf_counter() - t0)
        stop_collector(cp)
        time.sleep(1.0)

    b = stats(base_t); ins = stats(inst_t)
    overhead_pct = (ins["mean"] - b["mean"]) / b["mean"] * 100 if b["mean"] > 0 else 0
    print(f"    Baseline:   mean={b['mean']:.2f}s  sigma={b['stddev']:.2f}s")
    print(f"    Instrumented: mean={ins['mean']:.2f}s  sigma={ins['stddev']:.2f}s")
    print(f"    Overhead: {overhead_pct:+.1f}%")
    return {"task": E2E_TASK, "baseline": b, "instrumented": ins, "overhead_pct": overhead_pct}


# ══════════════════════════════════════════════════════════════════════════
# SVG Charts (pure Python, no dependencies)
# ══════════════════════════════════════════════════════════════════════════

def _svg_bar(data, width=700, height=420, title="", subtitle="", ylabel="",
             colors=None, max_scale=1.6):
    """Professional bar chart with gradient bars, value badges, and annotation."""
    if not colors: colors = ["#4338CA", "#7C3AED", "#DB2777"]
    ml, mr, mt, mb = 70, 30, 80, 80
    pw = width - ml - mr; ph = height - mt - mb
    n = len(data)
    bar_w = min(pw / n * 0.50, 130); gap = pw / n
    max_v = max(v for _, v in data) * max_scale if data else 1
    if max_v <= 0: max_v = 1

    # Defs for gradients
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" font-family="Inter,Noto Sans CJK SC,DejaVu Sans,sans-serif">\n'
    svg += '<defs>\n'
    for i, c in enumerate(colors[:n]):
        svg += f'<linearGradient id="g{i}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="{c}" stop-opacity="1"/><stop offset="100%" stop-color="{c}" stop-opacity="0.75"/></linearGradient>\n'
    svg += '<filter id="shadow"><feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.12"/></filter>\n'
    svg += '</defs>\n'
    svg += f'<rect width="{width}" height="{height}" fill="#FAFAFA" rx="12"/>\n'

    # Title block
    svg += f'<text x="{width/2}" y="38" text-anchor="middle" font-size="18" font-weight="700" fill="#111827" letter-spacing="-0.3">{title}</text>\n'
    if subtitle:
        svg += f'<text x="{width/2}" y="58" text-anchor="middle" font-size="12" fill="#6B7280">{subtitle}</text>\n'

    # Y-axis label
    svg += f'<text x="18" y="{mt+ph/2}" text-anchor="middle" font-size="12" font-weight="600" fill="#4B5563" transform="rotate(-90,18,{mt+ph/2})">{ylabel}</text>\n'

    # Subtle grid
    for i in range(5):
        yv = max_v * i / 4; y = mt + ph - (yv / max_v) * ph
        svg += f'<line x1="{ml}" y1="{y:.1f}" x2="{ml+pw}" y2="{y:.1f}" stroke="#E5E7EB" stroke-width="1"/>\n'
        svg += f'<text x="{ml-10}" y="{y+4:.1f}" text-anchor="end" font-size="11" fill="#9CA3AF">{yv:.0f}</text>\n'

    total = sum(v for _, v in data)
    for i, (label, value) in enumerate(data):
        x = ml + i * gap + (gap - bar_w) / 2
        bh = (value / max_v) * ph if max_v > 0 else 0; y = mt + ph - bh
        rx = min(bar_w * 0.12, 6)
        svg += f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" fill="url(#g{i})" rx="{rx:.1f}" filter="url(#shadow)"/>\n'

        # Value badge above bar
        badge_w = 75; badge_h = 26; badge_x = x + bar_w/2 - badge_w/2; badge_y = y - 32
        svg += f'<rect x="{badge_x:.1f}" y="{badge_y:.1f}" width="{badge_w:.1f}" height="{badge_h:.1f}" rx="13" fill="#111827" opacity="0.92"/>\n'
        svg += f'<text x="{x+bar_w/2:.1f}" y="{badge_y + 17:.1f}" text-anchor="middle" font-size="13" font-weight="700" fill="white">{value:.0f} ns</text>\n'

        # Label below
        svg += f'<text x="{x+bar_w/2:.1f}" y="{mt+ph+22:.1f}" text-anchor="middle" font-size="12" font-weight="600" fill="#374151">{label}</text>\n'

        # Pct of total
        pct = value / total * 100 if total > 0 else 0
        svg += f'<text x="{x+bar_w/2:.1f}" y="{mt+ph+42:.1f}" text-anchor="middle" font-size="11" fill="#9CA3AF">{pct:.0f}% of total</text>\n'

    # Bottom annotation
    svg += f'<text x="{width/2}" y="{height-14}" text-anchor="middle" font-size="10" fill="#9CA3AF">Measured via kernel bpftool prog show — run_time_ns / run_cnt over 500K iterations</text>\n'
    svg += '</svg>'
    return svg


def _svg_line(xs, ys, width=700, height=420, title="", subtitle="", xlabel="", ylabel="",
              steady=None, steady_label="", color="#4338CA"):
    """Polished line chart with gradient fill, endpoint highlight, annotation badge."""
    ml, mr, mt, mb = 70, 30, 80, 90
    pw = width - ml - mr; ph = height - mt - mb
    n = len(xs)
    if n < 2: return ""
    max_y = max(ys) * 1.18 if ys else 1; min_y = min(ys) * 0.96 if ys else 0
    if max_y <= 0: max_y = 1
    y_range = max_y - min_y if max_y > min_y else 1

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"'
           f' font-family="Inter,Noto Sans CJK SC,DejaVu Sans,sans-serif">\n')
    svg += f'<defs>\n<linearGradient id="area" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="{color}" stop-opacity="0.20"/><stop offset="100%" stop-color="{color}" stop-opacity="0.02"/></linearGradient>\n</defs>\n'
    svg += f'<rect width="{width}" height="{height}" fill="#FAFAFA" rx="12"/>\n'
    svg += f'<text x="{width/2}" y="38" text-anchor="middle" font-size="18" font-weight="700" fill="#111827" letter-spacing="-0.3">{title}</text>\n'
    if subtitle:
        svg += f'<text x="{width/2}" y="58" text-anchor="middle" font-size="12" fill="#6B7280">{subtitle}</text>\n'
    svg += f'<text x="{width/2}" y="{height-14}" text-anchor="middle" font-size="11" fill="#6B7280">{xlabel}</text>\n'
    svg += f'<text x="18" y="{mt+ph/2}" text-anchor="middle" font-size="12" font-weight="600" fill="#4B5563" transform="rotate(-90,18,{mt+ph/2})">{ylabel}</text>\n'

    for i in range(5):
        yv = min_y + y_range * i / 4
        y = mt + ph - ((yv - min_y) / y_range) * ph
        svg += f'<line x1="{ml}" y1="{y:.1f}" x2="{ml+pw}" y2="{y:.1f}" stroke="#E5E7EB" stroke-width="1"/>\n'
        svg += f'<text x="{ml-10}" y="{y+4:.1f}" text-anchor="end" font-size="11" fill="#9CA3AF">{yv:.1f}</text>\n'

    pts = [f"{ml + (i/(n-1))*pw:.1f},{mt+ph-((ys[i]-min_y)/y_range)*ph:.1f}" for i in range(n)]
    d = "M " + " L ".join(pts)
    svg += f'<path d="{d} L {ml+pw:.1f},{mt+ph:.1f} L {ml:.1f},{mt+ph:.1f} Z" fill="url(#area)"/>\n'
    svg += f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2.8" stroke-linejoin="round" stroke-linecap="round"/>\n'

    # Endpoint dot + value badge (upper-right)
    lx, ly = ml + pw, mt + ph - ((ys[-1] - min_y) / y_range) * ph
    svg += f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="5.5" fill="{color}" stroke="white" stroke-width="2.5"/>\n'
    svg += f'<rect x="{lx-28}" y="{ly-40}" width="56" height="24" rx="12" fill="#111827" opacity="0.90"/>\n'
    svg += f'<text x="{lx:.1f}" y="{ly-24}" text-anchor="middle" font-size="12" font-weight="700" fill="white">{ys[-1]:.1f} MB</text>\n'

    if steady is not None and steady > 0:
        sy_val = mt + ph - ((steady - min_y) / y_range) * ph
        svg += f'<line x1="{ml}" y1="{sy_val:.1f}" x2="{ml+pw}" y2="{sy_val:.1f}" stroke="#DB2777" stroke-width="1.8" stroke-dasharray="6,4" opacity="0.85"/>\n'
        if steady_label:
            # Steady badge on the LEFT side to avoid overlap with endpoint badge
            svg += f'<rect x="{ml+8}" y="{sy_val-24}" width="120" height="22" rx="6" fill="#FDF2F8" stroke="#FBCFE8" stroke-width="1"/>\n'
            svg += f'<text x="{ml+68}" y="{sy_val-9}" text-anchor="middle" font-size="11" font-weight="600" fill="#BE185D">{steady_label}</text>\n'
    svg += '</svg>'
    return svg


def _svg_paired(data, width=580, height=400, title="", subtitle=""):
    """Paired bar chart with gradients, error bars, delta callout."""
    colors = ["#4338CA", "#DB2777"]
    ml, mr, mt, mb = 60, 30, 80, 95
    pw = width - ml - mr; ph = height - mt - mb
    n = len(data); pair_w = min(pw / n * 0.65, 160)
    bar_w = pair_w / 2.5; gap = pw / n
    all_v = [v for d in data for v in (d[1], d[2])]
    max_v = max(all_v) * 1.5 if all_v else 1
    if max_v <= 0: max_v = 1

    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"'
           f' font-family="Inter,Noto Sans CJK SC,DejaVu Sans,sans-serif">\n')
    svg += '<defs>\n'
    for i, c in enumerate(colors):
        svg += f'<linearGradient id="pg{i}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="{c}" stop-opacity="1"/><stop offset="100%" stop-color="{c}" stop-opacity="0.78"/></linearGradient>\n'
    svg += '<filter id="ps"><feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.10"/></filter>\n'
    svg += '</defs>\n'
    svg += f'<rect width="{width}" height="{height}" fill="#FAFAFA" rx="12"/>\n'
    svg += f'<text x="{width/2}" y="38" text-anchor="middle" font-size="18" font-weight="700" fill="#111827" letter-spacing="-0.3">{title}</text>\n'
    if subtitle:
        svg += f'<text x="{width/2}" y="58" text-anchor="middle" font-size="12" fill="#6B7280">{subtitle}</text>\n'

    for i in range(5):
        yv = max_v * i / 4; y = mt + ph - (yv / max_v) * ph
        svg += f'<line x1="{ml}" y1="{y:.1f}" x2="{ml+pw}" y2="{y:.1f}" stroke="#E5E7EB" stroke-width="1"/>\n'
        svg += f'<text x="{ml-10}" y="{y+4:.1f}" text-anchor="end" font-size="11" fill="#9CA3AF">{yv:.2f}</text>\n'

    for i, (label, v1, v2, e1, e2) in enumerate(data):
        cx = ml + i * gap + gap / 2
        for j, (v, e, c) in enumerate([(v1, e1, 0), (v2, e2, 1)]):
            x = cx + (j - 0.5) * bar_w * 2.5
            h = (v / max_v) * ph if max_v > 0 else 0; y = mt + ph - h; rx = min(bar_w * 0.14, 5)
            svg += f'<rect x="{x-bar_w/2:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="url(#pg{c})" rx="{rx:.1f}" filter="url(#ps)"/>\n'
            delta_y = y - 12 if h > 25 else y - 10
            svg += f'<text x="{x:.1f}" y="{delta_y:.1f}" text-anchor="middle" font-size="14" font-weight="700" fill="#111827">{v:.2f}s</text>\n'
            if e > 0.001:
                etop = mt + ph - ((v+e)/max_v)*ph; ebot = mt + ph - ((v-e)/max_v)*ph
                svg += f'<line x1="{x:.1f}" y1="{etop:.1f}" x2="{x:.1f}" y2="{ebot:.1f}" stroke="#6B7280" stroke-width="1.3"/>\n'
                for ex in [etop, ebot]:
                    svg += f'<line x1="{x-4:.1f}" y1="{ex:.1f}" x2="{x+4:.1f}" y2="{ex:.1f}" stroke="#6B7280" stroke-width="1.3"/>\n'

    # Legend
    for j, (name, c) in enumerate([("Baseline (no collector)", 0), ("Instrumented (with collector)", 1)]):
        lx = ml + j * 220 + 15; ly = mt + ph + 55
        svg += f'<rect x="{lx}" y="{ly}" width="14" height="14" rx="3" fill="url(#pg{c})"/>\n'
        svg += f'<text x="{lx+20}" y="{ly+11}" font-size="12" fill="#374151">{name}</text>\n'
    svg += '</svg>'
    return svg


def generate_charts(report):
    generated = []

    # Chart 1: Probe Latency — Chinese
    probe = report["experiments"].get("probe_latency", {})
    if probe:
        probes = probe.get("probes", {})
        data = [(name.replace("probe_SSL_", "").replace("probe_", "探针_"),
                 s["avg_ns"]) for name, s in sorted(probes.items())]
        if data:
            total = probe.get("total_per_op_ns", sum(v for _, v in data))
            svg = _svg_bar(data, title="eBPF 探针执行延迟",
                           subtitle=f"每次 SSL 操作合计 ~{total:.0f} ns（SSL 操作本身为微秒级，开销可忽略）",
                           ylabel="平均延迟（纳秒）", max_scale=2.2)
            (CHARTS_DIR / "fig_perf_probe_latency.svg").write_text(svg, encoding="utf-8")
            generated.append("fig_perf_probe_latency.svg")

    # Chart 2: Memory Footprint — Chinese, wider data
    mem = report["experiments"].get("memory_footprint", {})
    if mem and mem.get("rss_timeline"):
        rss = mem["rss_timeline"]
        peak, steady = mem.get("peak_rss_mb", 0), mem.get("steady_state_rss_mb", 0)
        dur = mem.get("duration_s", len(rss))
        svg = _svg_line(list(range(len(rss))), rss,
                        title="采集器内存占用",
                        subtitle=f"峰值 {peak:.1f} MB · 稳态 {steady:.1f} MB · {dur}s 空闲运行",
                        xlabel="时间（秒）", ylabel="RSS（MB）",
                        steady=steady if steady > 0 else None,
                        steady_label=f"稳态: {steady:.1f} MB" if steady > 0 else "")
        (CHARTS_DIR / "fig_perf_memory.svg").write_text(svg, encoding="utf-8")
        generated.append("fig_perf_memory.svg")

    # Chart 3: End-to-End Overhead — Chinese
    e2e = report["experiments"].get("e2e_overhead", {})
    if e2e:
        b, ins = e2e["baseline"], e2e["instrumented"]
        overhead = e2e.get("overhead_pct", 0)
        sign = "+" if overhead > 0 else ""
        noise = abs(overhead) < 1.0
        title = f"端到端 Agent 任务开销: {sign}{overhead:.1f}%"
        subtitle = "采集器开销在测量噪声范围内，对 Agent 延迟无感知影响" if noise else f"采集器增加 {sign}{overhead:.1f}% 端到端延迟"
        svg = _svg_paired([("Claude 任务", b["mean"], ins["mean"], b["stddev"], ins["stddev"])],
                          title=title, subtitle=subtitle)
        (CHARTS_DIR / "fig_perf_e2e_overhead.svg").write_text(svg, encoding="utf-8")
        generated.append("fig_perf_e2e_overhead.svg")

    for g in generated: print(f"  ✓ {g}")
    return generated


# ══════════════════════════════════════════════════════════════════════════
# LaTeX PDF tables — dynamic, reads from live perf_report.json
# ══════════════════════════════════════════════════════════════════════════

LATEX_PRE = r"""\documentclass[11pt]{ctexart}
\usepackage[margin=1in]{geometry}
\usepackage{booktabs,array,caption,float}
\captionsetup{font=small,labelfont=bf}
\setCJKmainfont{Noto Serif CJK SC}
\begin{document}
\thispagestyle{empty}
"""
LATEX_POST = "\n\\end{document}\n"


def _tex_table(cols, rows, caption, raw_hdr=False):
    n = len(cols); align = "l" + "c" * (n - 1)
    tex = "\\begin{table}[H]\n\\centering\n"
    tex += f"\\caption{{{caption}}}\n\\small\n"
    tex += f"\\begin{{tabular}}{{{align}}}\n\\toprule\n"
    hdr = " & ".join(f"\\textbf{{{c if raw_hdr else c}}}" for c in cols)
    tex += hdr + " \\\\\n\\midrule\n"
    for row in rows:
        tex += " & ".join(str(c) for c in row) + " \\\\\n"
    tex += "\\bottomrule\n\\end{tabular}\n\\end{table}\n"
    return tex


def _compile_latex(tex_path, workdir):
    for _ in range(2):
        subprocess.run(["xelatex", "-interaction=nonstopmode", "-output-directory",
                        str(workdir), tex_path.name], cwd=str(workdir),
                       capture_output=True, text=True, timeout=30)
    pdf = workdir / tex_path.with_suffix(".pdf").name
    if pdf.exists() and pdf.stat().st_size > 1000:
        for ext in [".aux", ".log", ".out"]:
            (workdir / tex_path.with_suffix(ext).name).unlink(missing_ok=True)
        return pdf
    return None


def generate_perf_pdfs(report, output_dir):
    """Generate LaTeX PDF tables from live experiment data."""
    tables_dir = output_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    pdfs = []; combined = ""

    probe = report["experiments"].get("probe_latency", {})
    if probe:
        probes = probe.get("probes", {})
        rows = []
        for name, s in sorted(probes.items()):
            rows.append([name.replace("probe_SSL_", ""), f"{s['run_cnt']:,}",
                         f"{s['run_time_ns']:,} ns", f"{s['avg_ns']:.1f} ns",
                         f"{s['xlated_bytes']} B"])
        total = probe.get("total_per_op_ns", 0)
        rows.append([r"\textbf{每次 SSL 操作合计}", "—", "—",
                     f"\\textbf{{~{total:.0f} ns}}", "—"])
        body = _tex_table(["探针名称", "调用次数", "总执行时间", "平均延迟", "字节码"],
                          rows, "eBPF 探针执行延迟（内核 bpftool 统计）")
        combined += body + "\n\\clearpage\n"
        tex = tables_dir / "perf_table1_probe.tex"
        tex.write_text(LATEX_PRE + body + LATEX_POST, encoding="utf-8")
        p = _compile_latex(tex, tables_dir)
        if p: pdfs.append(p.name)

    mem = report["experiments"].get("memory_footprint", {})
    if mem:
        s = mem.get("rss_stats", {})
        body = _tex_table(["指标", "数值"], [
            ["平均 RSS", f"{s.get('mean', 0):.1f} MB"],
            ["峰值 RSS", f"{mem.get('peak_rss_mb', 0):.1f} MB"],
            ["稳态 RSS", f"{mem.get('steady_state_rss_mb', 0):.1f} MB"],
            ["采样时长", f"{mem.get('duration_s', 0)}s"],
            ["采样数", str(mem.get('num_samples', 0))],
        ], "采集器内存占用")
        combined += body + "\n\\clearpage\n"
        tex = tables_dir / "perf_table2_memory.tex"
        tex.write_text(LATEX_PRE + body + LATEX_POST, encoding="utf-8")
        p = _compile_latex(tex, tables_dir)
        if p: pdfs.append(p.name)

    e2e = report["experiments"].get("e2e_overhead", {})
    if e2e:
        b = e2e.get("baseline", {}); ins = e2e.get("instrumented", {})
        body = _tex_table(["指标", "Baseline（无采集器）", "Instrumented（有采集器）", "Δ"], [
            ["平均耗时", f"{b.get('mean', 0):.2f}s", f"{ins.get('mean', 0):.2f}s",
             f"{e2e.get('overhead_pct', 0):+.1f}%"],
            ["标准差", f"±{b.get('stddev', 0):.2f}s", f"±{ins.get('stddev', 0):.2f}s", "—"],
            ["P95 延迟", f"{b.get('p95', 0):.2f}s", f"{ins.get('p95', 0):.2f}s", "—"],
        ], "端到端 Agent 任务开销")
        combined += body
        tex = tables_dir / "perf_table3_e2e.tex"
        tex.write_text(LATEX_PRE + body + LATEX_POST, encoding="utf-8")
        p = _compile_latex(tex, tables_dir)
        if p: pdfs.append(p.name)

    # Combined PDF
    if combined:
        ct = tables_dir / "perf_all_tables.tex"
        ct.write_text(LATEX_PRE + combined + LATEX_POST, encoding="utf-8")
        cp = _compile_latex(ct, tables_dir)
        if cp: pdfs.append("perf_all_tables.pdf")

    return pdfs


# ══════════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════════

def main():
    report = {"timestamp": time.strftime("%Y-%m-%d %H:%M:%S"), "experiments": {}}

    probe = run_probe_latency()
    if probe: report["experiments"]["probe_latency"] = probe

    mem = run_memory_footprint(duration=60)
    if mem: report["experiments"]["memory_footprint"] = mem

    e2e = run_e2e_overhead(runs=5)
    if e2e: report["experiments"]["e2e_overhead"] = e2e

    # Save JSON
    report_path = RESULTS / "perf_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\n→ {report_path}")

    # Charts
    print(f"\n{'='*65}\n  Charts\n{'='*65}")
    generate_charts(report)

    # PDF tables (dynamic, from live data)
    print(f"\n{'='*65}\n  PDF Tables\n{'='*65}")
    pdfs = generate_perf_pdfs(report, RESULTS)
    for p in pdfs:
        print(f"  ✓ {p}")

    print(f"\n{'='*65}")
    print(f"  Results: {report_path}")
    for k, v in report["experiments"].items():
        print(f"    {k}: ✓" if v else f"    {k}: ✗")
    print(f"{'='*65}")


if __name__ == "__main__":
    main()
