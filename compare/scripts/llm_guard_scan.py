#!/usr/bin/env python3
"""
LLM Guard 模型化扫描 — 使用本地 compare/models/ 中的 DeBERTa 模型。

用法:
    compare/.venv-llm-guard/bin/python llm_guard_scan.py <prompt_text_file>
输出: JSON findings 数组到 stdout
"""
import json, os, sys, torch
from pathlib import Path
from transformers import AutoTokenizer, AutoModelForSequenceClassification

# 本地模型路径 (compare/models/prompt-injection-deberta-v3)。
# HISTORICAL: ONNX 运行时已于 2026-10-27 移除；用 LLM_GUARD_MODEL 指向实际目录，
# 否则给出明确错误而不是让 transformers 报找不到文件。
_LOCAL_MODEL = Path(os.environ.get(
    "LLM_GUARD_MODEL", str(Path(__file__).resolve().parents[1] / "models/prompt-injection-deberta-v3")))
if not _LOCAL_MODEL.is_dir():
    print(f"llm_guard_scan: model directory missing: {_LOCAL_MODEL} "
          "(set LLM_GUARD_MODEL; the bundled ONNX model was removed on 2026-10-27)",
          file=sys.stderr)
    json.dump([], sys.stdout)
    sys.exit(2)

text_file = Path(sys.argv[1])
if not text_file.exists():
    json.dump([], sys.stdout)
    sys.exit(0)

text = text_file.read_text(encoding="utf-8", errors="replace")

findings = []
try:
    tokenizer = AutoTokenizer.from_pretrained(str(_LOCAL_MODEL), local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(str(_LOCAL_MODEL), local_files_only=True)
    model.eval()
    # Official LLM Guard PromptInjection v2 behaviour: 512-token windows with
    # 128-token stride, threshold 0.92, max risk over windows.
    windows = tokenizer(text, truncation=True, max_length=512, stride=128,
                        return_overflowing_tokens=True)["input_ids"]
    risks = []
    for window in windows:
        inputs = tokenizer.decode(window, skip_special_tokens=True)
        enc = tokenizer(inputs, return_tensors="pt", truncation=True, max_length=512)
        with torch.no_grad():
            probs = torch.softmax(model(**enc).logits, dim=-1)[0]
        # id2label: 0=SAFE, 1=INJECTION
        risks.append(float(probs[1]))
    risk_score = max(risks) if risks else 0.0
    if risk_score >= 0.92:
        findings.append({
            "scanner": "prompt_injection",
            "category": "prompt_injection",
            "is_valid": False,
            "risk_score": risk_score,
            "threshold": 0.92,
            "windows": len(risks),
            "detail": f"max injection probability {risk_score:.4f} >= 0.92",
        })
except Exception as e:
    print(f"[ERROR] {e}", file=sys.stderr)

json.dump(findings, sys.stdout)
