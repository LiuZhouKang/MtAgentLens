#!/usr/bin/env python3
"""Meta Prompt Guard batch evaluation on MtAgentLens FuxiBench prompt_inputs.

Loads Prompt-Guard-86M from local compare/models/prompt-guard-86m, classifies
each scenario's FULL prompt_text (task + fixture content),
and produces per-type metrics (TPR/FPR) for Type A, B, C, and benign scenarios.

Output: compare/results/external/prompt_baselines/FuxiBench/meta_prompt_guard/

Usage:
    cd /home/lzk/MtAgentLens
    python3 compare/scripts/meta_prompt_guard_batch.py         # Full batch (86 scenarios)
    python3 compare/scripts/meta_prompt_guard_batch.py --dry 3 # Test with 3 scenarios
"""

from __future__ import annotations

import json
import os
import sys
import time
import argparse
from collections import defaultdict
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT = Path(__file__).resolve().parents[2]  # compare/ → mtagentlens/
COMPARE = PROJECT / "compare"
MANIFEST_PATH = COMPARE / "Dataset/FuxiBench/prompt_inputs/manifest.json"
# prompt_text_path values already include "cases/" prefix, so CASES_BASE is the
# prompt_inputs directory (parent of cases/).
CASES_BASE = COMPARE / "Dataset/FuxiBench/prompt_inputs"
OUTPUT_DIR = COMPARE / "results/external/prompt_baselines/FuxiBench/meta_prompt_guard"

# ── Model configuration ────────────────────────────────────────────────────────
# 项目本地模型目录
PROJECT_MODELS = COMPARE / "models"
PRIMARY_MODEL = str(PROJECT_MODELS / "prompt-guard-86m")
FALLBACK_MODEL = str(PROJECT_MODELS / "prompt-injection-deberta-v3")

# Meta Prompt Guard label mapping (3-class: benign, injection, jailbreak)
# Both "injection" and "jailbreak" map to "attack" prediction
META_LABEL_MAP = {
    0: "benign",
    1: "injection",
    2: "jailbreak",
}

# Fallback label mapping (2-class: SAFE, INJECTION)
FALLBACK_LABEL_MAP = {
    0: "benign",
    1: "injection",
}

ATTACK_LABELS = {"injection", "jailbreak", "INJECTION"}


# ── Model loading ──────────────────────────────────────────────────────────────
def load_model(local_only: bool = True):
    """Load the Prompt Guard model. Tries primary first, then fallback.

    Uses local_files_only=True to avoid network calls (model must be pre-downloaded).
    Set local_only=False to allow download from HuggingFace Hub.
    """
    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification

    for model_name, label_map, source in [
        (PRIMARY_MODEL, META_LABEL_MAP, "Meta Prompt Guard 86M (Niansuh mirror)"),
        (FALLBACK_MODEL, FALLBACK_LABEL_MAP, "protectai DeBERTa-v3 prompt injection"),
    ]:
        try:
            print(f"[load] Trying {source}")
            print(f"[load]   Model: {model_name}")
            tokenizer = AutoTokenizer.from_pretrained(
                model_name, local_files_only=local_only
            )
            model = AutoModelForSequenceClassification.from_pretrained(
                model_name, local_files_only=local_only
            )
            model.eval()

            device = "cuda" if torch.cuda.is_available() else "cpu"
            model = model.to(device)
            print(f"[load]   Device: {device}")
            print(f"[load]   Labels: {model.config.id2label}")
            print(f"[load] SUCCESS: {source}")
            return tokenizer, model, label_map, device, model_name, source
        except Exception as e:
            print(f"[load] FAILED: {e}")
            continue

    raise RuntimeError(
        "Could not load any Prompt Guard model. "
        "Ensure the model is pre-downloaded to HuggingFace cache "
        "(~/.cache/huggingface/hub/) or set local_only=False with network access."
    )


# ── Classification ─────────────────────────────────────────────────────────────
def classify_text(
    text: str,
    tokenizer,
    model,
    label_map: dict[int, str],
    device: str,
    max_length: int = 512,
) -> tuple[str, dict[int, float]]:
    """Classify a single text and return (label, {label_id: probability})."""
    import torch

    inputs = tokenizer(
        text,
        return_tensors="pt",
        truncation=True,
        max_length=max_length,
        padding=False,
    )
    inputs = {k: v.to(device) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = model(**inputs)
        probs = torch.softmax(outputs.logits, dim=-1).squeeze(0)

    pred_id = torch.argmax(probs, dim=-1).item()
    label = label_map.get(pred_id, f"class_{pred_id}")
    prob_dict = {i: float(p) for i, p in enumerate(probs.tolist())}

    return label, prob_dict


# ── Manifest loading ───────────────────────────────────────────────────────────
def load_manifest() -> list[dict]:
    """Load and validate the prompt_inputs manifest."""
    with open(MANIFEST_PATH) as f:
        manifest = json.load(f)
    entries = manifest.get("entries", [])
    print(f"[manifest] Loaded {len(entries)} scenarios from {MANIFEST_PATH}")

    # Show distribution
    from collections import Counter
    type_dist = Counter(e["attack_type"] for e in entries)
    print(f"[manifest] Attack type distribution: {dict(type_dist)}")
    return entries


# ── Prompt text loading ────────────────────────────────────────────────────────
def load_prompt_text(entry: dict) -> str:
    """Read the FULL prompt_text (task + fixture content) for a scenario."""
    prompt_path = CASES_BASE / entry["prompt_text_path"]
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt text not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8")


# ── Prediction mapping ─────────────────────────────────────────────────────────
def pred_to_binary(label: str) -> str:
    """Map model label to binary attack/benign prediction."""
    if label in ATTACK_LABELS:
        return "attack"
    return "benign"


# ── Metrics computation ────────────────────────────────────────────────────────
def compute_metrics(
    results: list[dict],
) -> dict:
    """Compute per-type and overall metrics."""
    from collections import defaultdict

    # Per-type counters: {type: {"tp": N, "fp": N, "fn": N, "tn": N}}
    cm = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "tn": 0})

    for r in results:
        gt = r["ground_truth"]      # "attack" or "benign" (from attack_type mapping)
        pred = r["prediction"]      # "attack" or "benign"
        atype = r["attack_type"]    # "A", "B", "C", "benign"

        key = atype
        if pred == "attack" and gt == "attack":
            cm[key]["tp"] += 1
        elif pred == "attack" and gt == "benign":
            cm[key]["fp"] += 1
        elif pred == "benign" and gt == "attack":
            cm[key]["fn"] += 1
        else:
            cm[key]["tn"] += 1

    # Also compute overall
    all_key = "all"
    cm[all_key] = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for key in ["A", "B", "C", "benign"]:
        for m in ["tp", "fp", "fn", "tn"]:
            cm[all_key][m] += cm[key][m]

    def safe_div(a, b):
        return a / b if b > 0 else 0.0

    metrics = {}
    for key in ["A", "B", "C", "benign", "all"]:
        c = cm[key]
        tp, fp, fn, tn = c["tp"], c["fp"], c["fn"], c["tn"]
        tpr = safe_div(tp, tp + fn)   # Recall / True Positive Rate
        tnr = safe_div(tn, tn + fp)   # True Negative Rate
        fpr = safe_div(fp, fp + tn)   # False Positive Rate
        fnr = safe_div(fn, fn + tp)   # False Negative Rate
        precision = safe_div(tp, tp + fp)
        f1 = safe_div(2 * precision * tpr, precision + tpr)
        accuracy = safe_div(tp + tn, tp + fp + fn + tn)
        total = tp + fp + fn + tn

        metrics[key] = {
            "total": total,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "tpr": round(tpr, 4),
            "tnr": round(tnr, 4),
            "fpr": round(fpr, 4),
            "fnr": round(fnr, 4),
            "precision": round(precision, 4),
            "f1": round(f1, 4),
            "accuracy": round(accuracy, 4),
        }

    return metrics


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Meta Prompt Guard batch evaluation")
    parser.add_argument(
        "--dry", type=int, default=0,
        help="Run only first N scenarios (dry-run mode)"
    )
    parser.add_argument(
        "--max-length", type=int, default=512,
        help="Max token length for classification (default: 512)"
    )
    parser.add_argument(
        "--batch-size", type=int, default=1,
        help="Batch size (default: 1, sequential)"
    )
    parser.add_argument(
        "--no-local", action="store_true",
        help="Allow network access to download model (default: local files only)"
    )
    args = parser.parse_args()

    # ── Setup ────────────────────────────────────────────────────────────────
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[setup] Output directory: {OUTPUT_DIR}")

    # Load model
    tokenizer, model, label_map, device, model_name, source = load_model(
        local_only=not args.no_local
    )
    model_info = {
        "model_name": model_name,
        "source": source,
        "device": device,
        "labels": {str(k): v for k, v in label_map.items()},
        "max_length": args.max_length,
    }
    print(f"[setup] Model info: {json.dumps(model_info, indent=2)}")

    # Load manifest
    entries = load_manifest()
    if args.dry > 0:
        entries = entries[:args.dry]
        print(f"[dry-run] Limiting to {len(entries)} scenarios")

    # ── Run classification ───────────────────────────────────────────────────
    results = []
    start_time = time.time()

    for i, entry in enumerate(entries):
        sid = entry["scenario_id"]
        atype = entry["attack_type"]

        # Map attack_type to binary ground truth
        gt = "benign" if atype == "benign" else "attack"

        try:
            prompt_text = load_prompt_text(entry)
        except FileNotFoundError as e:
            print(f"[{i+1:3d}/{len(entries)}] {sid:40s} ERROR: missing prompt.txt")
            results.append({
                "scenario_id": sid,
                "attack_type": atype,
                "ground_truth": gt,
                "model_label": "error",
                "prediction": "error",
                "probabilities": {},
                "error": str(e),
                "prompt_text_sha256": entry.get("prompt_text_sha256", ""),
            })
            continue

        label, probs = classify_text(
            prompt_text, tokenizer, model, label_map, device,
            max_length=args.max_length,
        )
        prediction = pred_to_binary(label)

        result = {
            "scenario_id": sid,
            "attack_type": atype,
            "category": entry.get("category", ""),
            "ground_truth": gt,
            "model_label": label,
            "prediction": prediction,
            "probabilities": {label_map.get(k, f"class_{k}"): v for k, v in probs.items()},
            "prompt_text_sha256": entry["prompt_text_sha256"],
            "prompt_text_length": len(prompt_text),
        }
        results.append(result)

        status = "✓" if prediction == gt else "✗"
        label_short = label[:12]
        print(
            f"[{i+1:3d}/{len(entries)}] {sid:40s} "
            f"gt={gt:7s} pred={prediction:7s} label={label_short:12s} "
            f"probs={probs} {status}"
        )

    elapsed = time.time() - start_time
    print(f"\n[timing] {len(entries)} scenarios in {elapsed:.1f}s "
          f"({elapsed/len(entries):.2f}s per scenario)")

    # ── Save individual results ───────────────────────────────────────────────
    individual_dir = OUTPUT_DIR / "individual"
    individual_dir.mkdir(parents=True, exist_ok=True)
    for r in results:
        out_path = individual_dir / f"{r['scenario_id']}.json"
        with open(out_path, "w") as f:
            json.dump(r, f, indent=2, ensure_ascii=False)

    print(f"[save] {len(results)} individual results saved to {individual_dir}")

    # ── Compute and save metrics ──────────────────────────────────────────────
    valid_results = [r for r in results if r["prediction"] != "error"]
    metrics = compute_metrics(valid_results)

    # Build summary
    summary = {
        "schema": "mtagentlens.meta_prompt_guard_batch.v1",
        "model": model_info,
        "dataset": "FuxiBench",
        "total_scenarios": len(entries),
        "total_valid": len(valid_results),
        "total_errors": len(results) - len(valid_results),
        "elapsed_seconds": round(elapsed, 2),
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "attack_label_mapping": {
            "attack_labels": list(ATTACK_LABELS),
            "mapped_to": "attack",
        },
        "metrics": metrics,
        "results": results,
    }

    # Save summary JSON
    summary_path = OUTPUT_DIR / "results.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"[save] Summary saved to {summary_path}")

    # ── Print metrics table ───────────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"  Meta Prompt Guard — FuxiBench Batch Evaluation Results")
    print(f"  Model: {source}")
    print(f"  Device: {device}  |  Max token length: {args.max_length}")
    print(f"{'='*80}")
    print(f"  {'Type':<10} {'N':>5} {'TPR':>8} {'FPR':>8} {'F1':>8} {'ACC':>8} "
          f"{'TP':>5} {'FP':>5} {'FN':>5} {'TN':>5}")
    print(f"  {'-'*70}")

    for key in ["A", "B", "C", "benign", "all"]:
        m = metrics[key]
        print(
            f"  {key:<10} {m['total']:>5} {m['tpr']:>8.4f} {m['fpr']:>8.4f} "
            f"{m['f1']:>8.4f} {m['accuracy']:>8.4f} "
            f"{m['tp']:>5} {m['fp']:>5} {m['fn']:>5} {m['tn']:>5}"
        )

    print(f"\n  Key findings:")
    print(f"    Attack TPR (Type A+B+C): {metrics['all']['tpr']:.4f}")
    print(f"    Benign FPR:               {metrics['benign']['fpr']:.4f}")
    print(f"    Overall accuracy:         {metrics['all']['accuracy']:.4f}")

    # ── Print per-scenario summary ────────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"  Per-Scenario Predictions")
    print(f"{'='*80}")
    correct = sum(1 for r in results if r["prediction"] == r["ground_truth"])
    incorrect = sum(1 for r in results if r["prediction"] != r["ground_truth"] and r["prediction"] != "error")
    errors = sum(1 for r in results if r["prediction"] == "error")
    print(f"  Correct: {correct}  |  Incorrect: {incorrect}  |  Errors: {errors}")

    if incorrect > 0:
        print(f"\n  Misclassifications:")
        for r in results:
            if r["prediction"] != r["ground_truth"] and r["prediction"] != "error":
                print(f"    {r['scenario_id']:40s} gt={r['ground_truth']:7s} "
                      f"pred={r['prediction']:7s} label={r['model_label']}")

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
