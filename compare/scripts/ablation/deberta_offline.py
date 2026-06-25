#!/usr/bin/env python3
"""
离线 DeBERTa 评估 — 对 FuxiBench 的所有 prompt 文本运行 ONNX 推理。

目的: 在不需要重新采集 eBPF 数据的情况下，为消融实验的 L3 (AI Models) 层
提供 DeBERTa D2Content 分数。DeBERTa 模型 detect prompt injection，对 prompt
文本推理与对 agent intent 推理在检测注入模式上高度一致。

输出: compare/results/paper/deberta_offline_scores.json
  { "scenario_id": 0.873, ... }

用法: python3 compare/scripts/ablation/deberta_offline.py
"""

import json
import os
import sys
import time
from pathlib import Path

import numpy as np

PROJECT = Path(__file__).resolve().parents[3]
MODEL_DIR = PROJECT / "collector/models/deberta"
MANIFEST = PROJECT / "compare/Dataset/FuxiBench/prompt_inputs/manifest.json"
PROMPT_DIR = PROJECT / "compare/Dataset/FuxiBench/prompt_inputs"
OUTPUT = PROJECT / "compare/results/paper/deberta_offline_scores.json"


def load_model(model_dir: str):
    """Load ONNX model + tokenizer."""
    import onnxruntime as ort
    from transformers import AutoTokenizer

    model_path = os.path.join(model_dir, "model.onnx")
    if not os.path.exists(model_path):
        print(f"ERROR: model.onnx not found at {model_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[deberta_offline] Loading tokenizer...", file=sys.stderr)
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    print(f"[deberta_offline] Loading ONNX model...", file=sys.stderr)
    session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    print(f"[deberta_offline] Ready", file=sys.stderr)
    return session, tokenizer


def predict(session, tokenizer, text: str) -> float:
    """Return injection probability [0, 1]."""
    inputs = tokenizer(
        text,
        return_tensors="np",
        max_length=512,
        truncation=True,
        padding="max_length",
    )
    outputs = session.run(
        None,
        {"input_ids": inputs["input_ids"], "attention_mask": inputs["attention_mask"]},
    )
    logits = outputs[0][0]
    # Softmax: injection score
    m = max(logits[0], logits[1])
    exp_clean = np.exp(logits[0] - m)
    exp_inject = np.exp(logits[1] - m)
    total = exp_clean + exp_inject
    return float(exp_inject / total) if total > 0 else 0.0


def main():
    print("=" * 80, file=sys.stderr)
    print("  DeBERTa 离线评估 — FuxiBench 全量 prompt 推理", file=sys.stderr)
    print("=" * 80, file=sys.stderr)

    # Load model
    session, tokenizer = load_model(str(MODEL_DIR))

    # Load manifest
    manifest = json.loads(MANIFEST.read_text())
    entries = manifest["entries"]
    print(f"[deberta_offline] {len(entries)} scenarios to process", file=sys.stderr)

    scores = {}
    stats = {"total": 0, "high": 0, "low": 0, "errors": 0}

    t_start = time.perf_counter()
    for i, entry in enumerate(entries):
        sid = entry["scenario_id"]
        at = entry.get("attack_type", "benign")
        prompt_rel = entry.get("prompt_text_path", "")
        prompt_path = PROMPT_DIR / prompt_rel

        if not prompt_path.exists():
            print(f"  [{i+1}/{len(entries)}] {sid}: prompt not found, skip", file=sys.stderr)
            stats["errors"] += 1
            continue

        prompt_text = prompt_path.read_text(encoding="utf-8")
        # Truncate to ~2000 chars (model limit is 512 tokens ~ 2000 chars)
        prompt_text = prompt_text[:2000]

        try:
            score = predict(session, tokenizer, prompt_text)
            scores[sid] = {
                "deberta_score": round(score, 4),
                "attack_type": at,
            }
            stats["total"] += 1
            if score > 0.5:
                stats["high"] += 1
            else:
                stats["low"] += 1

            if (i + 1) % 10 == 0:
                elapsed = time.perf_counter() - t_start
                rate = (i + 1) / elapsed
                print(f"  [{i+1}/{len(entries)}] {sid}: {score:.4f} ({at}) — {rate:.1f} samples/s",
                      file=sys.stderr)
        except Exception as e:
            print(f"  [{i+1}/{len(entries)}] {sid}: ERROR {e}", file=sys.stderr)
            stats["errors"] += 1

    elapsed = time.perf_counter() - t_start
    print(f"\n[deberta_offline] Done: {stats['total']} scored, "
          f"{stats['high']} high (score>0.5), {stats['low']} low, "
          f"{stats['errors']} errors in {elapsed:.1f}s",
          file=sys.stderr)

    # Breakdown by attack type
    by_type = {}
    for sid, v in scores.items():
        at = v["attack_type"]
        by_type.setdefault(at, []).append(v["deberta_score"])

    print("\n  按攻击类型的 DeBERTa 分数分布:", file=sys.stderr)
    for at in ["A", "B", "C", "benign"]:
        vals = by_type.get(at, [])
        if vals:
            print(f"    Type {at}: mean={np.mean(vals):.4f} median={np.median(vals):.4f} "
                  f"min={np.min(vals):.4f} max={np.max(vals):.4f} (n={len(vals)})",
                  file=sys.stderr)

    # Save
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "schema": "mtagentlens.deberta_offline.v1",
        "model": "protectai/deberta-base-prompt-injection-v2",
        "n_scored": stats["total"],
        "n_high": stats["high"],
        "n_low": stats["low"],
        "scores": scores,
        "by_type": {at: {
            "mean": float(np.mean(vals)),
            "median": float(np.median(vals)),
            "min": float(np.min(vals)),
            "max": float(np.max(vals)),
            "n": len(vals),
        } for at, vals in by_type.items()},
    }, ensure_ascii=False, indent=2))
    print(f"\n→ {OUTPUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
