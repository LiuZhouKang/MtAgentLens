#!/usr/bin/env python3
"""
LLM Guard 模型化扫描 — 使用本地 compare/models/ 中的 DeBERTa 模型。

用法:
    compare/.venv-llm-guard/bin/python llm_guard_scan.py <prompt_text_file>
输出: JSON findings 数组到 stdout
"""
import json, sys, torch
from pathlib import Path
from transformers import AutoTokenizer, AutoModelForSequenceClassification

# 本地模型路径 (compare/models/prompt-injection-deberta-v3)
_LOCAL_MODEL = Path("/home/lzk/MtAgentPrism/compare/models/prompt-injection-deberta-v3")

text_file = Path(sys.argv[1])
if not text_file.exists():
    json.dump([], sys.stdout)
    sys.exit(0)

text = text_file.read_text(encoding="utf-8", errors="replace")[:10000]

findings = []
try:
    tokenizer = AutoTokenizer.from_pretrained(str(_LOCAL_MODEL), local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(str(_LOCAL_MODEL), local_files_only=True)

    inputs = tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
    with torch.no_grad():
        outputs = model(**inputs)
        probs = torch.softmax(outputs.logits, dim=-1)
        pred = torch.argmax(probs, dim=-1).item()
        conf = float(probs[0][pred])

    # label 0 = benign, label 1 = injection
    risk_score = conf if pred == 1 else 1.0 - conf
    findings.append({
        "scanner": "prompt_injection",
        "category": "prompt_injection",
        "is_valid": pred == 0,
        "risk_score": risk_score,
        "confidence": conf,
        "severity": "High" if risk_score > 0.7 else ("Warning" if risk_score > 0.5 else "Low"),
        "detail": f"pred={pred} conf={conf:.4f}",
    })
except Exception as e:
    print(f"[ERROR] {e}", file=sys.stderr)

json.dump(findings, sys.stdout)
