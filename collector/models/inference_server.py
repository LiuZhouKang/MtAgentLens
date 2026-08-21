#!/usr/bin/env python3
"""ONNX DeBERTa inference server for AgentSight ContentBoundary Layer 3.

Loads ProtectAI DeBERTa-prompt-injection-v2 ONNX model at startup,
serves inference requests via JSON-line protocol over stdin/stdout.

Model: protectai/deberta-base-prompt-injection-v2 (Apache 2.0)
  - SOTA prompt injection / jailbreak detection on standard benchmarks
  - DeBERTa architecture, fine-tuned on multi-source injection datasets

Protocol:
  Request:  {"text": "<tool input text to classify>"}
  Response: {"score": 0.99, "label": "INJECTION", "elapsed_us": 450000}
  Error:    {"error": "<message>"}
  Ready:    {"status": "ready"}

Usage:
  python3 inference_server.py <model_dir>
"""

import json
import sys
import time
import os


def load_model(model_dir: str):
    """Load ONNX model + tokenizer."""
    import onnxruntime as ort
    from transformers import AutoTokenizer

    model_path = os.path.join(model_dir, "model.onnx")
    if not os.path.exists(model_path):
        sys.stderr.write(f"model.onnx not found at {model_path}\n")
        sys.exit(1)

    sys.stderr.write(f"[inference_server] Loading tokenizer...\n")
    tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True)
    sys.stderr.write(f"[inference_server] Loading ONNX model...\n")
    session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
    sys.stderr.write(f"[inference_server] Ready\n")
    return session, tokenizer


def predict(session, tokenizer, text: str) -> dict:
    """Run inference. Returns {"score": float, "label": str, "elapsed_us": int}."""
    t0 = time.perf_counter()
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
    logits = outputs[0][0]  # [clean_logit, injection_logit]
    m = max(logits[0], logits[1])
    exp_clean = 2.718281828459045 ** (logits[0] - m)
    exp_inject = 2.718281828459045 ** (logits[1] - m)
    total = exp_clean + exp_inject
    score = float(exp_inject / total) if total > 0 else 0.0

    elapsed_us = int((time.perf_counter() - t0) * 1_000_000)
    label = "INJECTION" if score >= 0.5 else "CLEAN"
    return {"score": score, "label": label, "elapsed_us": elapsed_us}


def main():
    if len(sys.argv) < 2:
        sys.stderr.write(
            f"Usage: {sys.argv[0]} <model_dir>\n"
            f"  model: protectai/deberta-base-prompt-injection-v2\n"
        )
        sys.exit(1)

    model_dir = sys.argv[1]
    session, tokenizer = load_model(model_dir)

    print(json.dumps({"status": "ready"}), flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            text = req.get("text", "")
            if not text:
                print(json.dumps({"error": "empty text"}), flush=True)
                continue
            result = predict(session, tokenizer, text)
            print(json.dumps(result), flush=True)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"json decode: {e}"}), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)}), flush=True)

    sys.stderr.write("[inference_server] stdin closed, shutting down\n")


if __name__ == "__main__":
    main()
