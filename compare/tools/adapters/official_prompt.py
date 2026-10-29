"""Official prompt guard engines. Missing models are failures, never regex substitutes."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from compare.tools.base import AdapterCase, AdapterResult, BoundaryType, FindingRecord, RunStatus

ROOT = Path(__file__).resolve().parents[3]


class OfficialPromptAdapter:
    engine: str

    def __init__(self, metadata):
        self.metadata = metadata

    def supports(self, case: AdapterCase) -> bool:
        prompt = case.input_artifacts.get("prompt_text")
        return prompt is not None and prompt.is_file()

    def run(self, case: AdapterCase, output_dir: Path) -> AdapterResult:
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", case.scenario_id) or ".." in case.scenario_id:
            raise ValueError("scenario_id must be a safe filename component")
        start = time.monotonic()
        output_dir.mkdir(parents=True, exist_ok=True)
        raw = output_dir / f"{case.scenario_id}.{self.engine}.json"
        stderr = output_dir / f"{case.scenario_id}.{self.engine}.stderr.txt"
        status, findings, failure, code = RunStatus.FAILED, [], None, None
        if not self.supports(case):
            failure = "A frozen prompt_text artifact is required"
        else:
            key = self.engine.upper().replace("-", "_")
            default = ROOT / f".venv-{self.engine}" / "bin/python"
            if not default.exists():
                default = ROOT / "compare" / f".venv-{self.engine}" / "bin/python"
            interpreter = os.environ.get(f"MTAGENTLENS_{key}_PYTHON", str(default) if default.exists() else sys.executable)
            env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                "PYTHONPATH": str(ROOT) + os.pathsep + os.environ.get("PYTHONPATH", "")}
            try:
                completed = subprocess.run([interpreter, "-m", "compare.tools.adapters.official_prompt", self.engine,
                    str(case.input_artifacts["prompt_text"])], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, check=False)
                code = completed.returncode
                raw.write_text(completed.stdout, encoding="utf-8")
                stderr.write_text(completed.stderr, encoding="utf-8")
                if code:
                    failure = completed.stderr[-1000:] or f"Official engine exited with status {code}"
                else:
                    payload = json.loads(completed.stdout)
                    if not isinstance(payload, dict) or not isinstance(payload.get("engine"), dict) or not isinstance(payload.get("findings"), list):
                        raise ValueError("Official engine returned an invalid result/provenance object")
                    if payload["engine"].get("name") != self.engine:
                        raise ValueError("Official engine identity does not match the requested tool")
                    for index, item in enumerate(payload["findings"]):
                        confidence = float(item["confidence"])
                        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                            raise ValueError("Official engine returned an invalid confidence")
                        findings.append(FindingRecord(self.metadata.id, case.dataset_name, case.scenario_id,
                            f"{case.scenario_id}.{self.engine}.{index}", "Warning", item["category"],
                            BoundaryType.CONTENT, None, self.engine, item.get("detail"),
                            json.dumps({"finding": item, "engine": payload["engine"]}, ensure_ascii=False), confidence, raw))
                    status = RunStatus.SUCCESS if findings else RunStatus.NO_FINDINGS
            except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
                failure = f"Official engine failed: {type(exc).__name__}: {exc}"
                findings = []
        return AdapterResult(self.metadata, case.scenario_id, status, tuple(findings),
            raw if raw.exists() else None, raw if raw.exists() else None, stderr if stderr.exists() else None,
            code, time.monotonic() - start, failure)


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _llm_guard(text: str) -> dict:
    from llm_guard.input_scanners import PromptInjection
    from llm_guard.input_scanners.prompt_injection import MatchType, V2_MODEL
    version = importlib.metadata.version("llm-guard")
    if version != "0.3.16":
        raise ValueError("Expected pinned llm-guard==0.3.16")
    # The SDK returns a tuple, not an object with is_valid/risk_score fields.
    scanner = PromptInjection(model=V2_MODEL, threshold=0.92, match_type=MatchType.FULL)
    tokenizer = scanner._pipeline.tokenizer
    windows = tokenizer(text, truncation=True, max_length=512, stride=128,
        return_overflowing_tokens=True)["input_ids"]
    valid, risk = True, -1.0
    for window in windows:
        _, window_valid, window_risk = scanner.scan(tokenizer.decode(window, skip_special_tokens=True))
        if not math.isfinite(float(window_risk)):
            raise ValueError("LLM Guard returned a non-finite risk score")
        valid = valid and window_valid
        risk = max(risk, float(window_risk))
    return {"engine": {"name": "llm-guard", "version": version, "model": V2_MODEL.path,
            "revision": V2_MODEL.revision, "threshold": 0.92, "match_type": "full_per_token_window",
            "window_tokens": 512, "stride_tokens": 128, "windows": len(windows)},
        "findings": [] if valid else [{"category": "prompt_injection", "confidence": max(0, min(1, float(risk))), "detail": "Official PromptInjection scanner rejected the input"}]}


def _meta_prompt_guard(text: str) -> dict:
    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    model_path = Path(os.environ["MTAGENTLENS_META_PROMPT_GUARD_MODEL"])
    expected = os.environ["MTAGENTLENS_META_PROMPT_GUARD_SHA256"]
    if not model_path.is_dir() or _tree_hash(model_path) != expected:
        raise ValueError("Meta Prompt Guard local model snapshot hash mismatch")
    tokenizer = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(model_path, local_files_only=True)
    model.eval()
    labels = {int(key): str(value).upper() for key, value in model.config.id2label.items()}
    indices = [index for index, label in labels.items() if label in ("INJECTION", "JAILBREAK")]
    if not indices:
        raise ValueError("Unsupported Prompt Guard label mapping; expected INJECTION/JAILBREAK")
    encoded = tokenizer(text, return_tensors="pt", truncation=True, max_length=512, stride=128,
        return_overflowing_tokens=True, padding=True)
    encoded.pop("overflow_to_sample_mapping", None)
    window_count = len(encoded["input_ids"])
    score = 0.0
    with torch.no_grad():
        for offset in range(0, window_count, 16):
            batch = {key: value[offset:offset + 16] for key, value in encoded.items()}
            scores = model(**batch).logits.softmax(dim=-1)[:, indices].sum(dim=-1)
            score = max(score, float(scores.max()))
    return {"engine": {"name": "meta-prompt-guard", "model_sha256": expected, "labels": labels,
            "threshold": 0.5, "window_tokens": 512, "stride_tokens": 128, "windows": window_count},
        "findings": [] if score < 0.5 else [{"category": "prompt_injection", "confidence": score, "detail": "Official Prompt Guard model INJECTION/JAILBREAK probability"}]}


def _vigil(text: str) -> dict:
    import vigil
    from vigil.vigil import Vigil
    config = Path(os.environ["MTAGENTLENS_VIGIL_CONFIG"])
    revision = os.environ["MTAGENTLENS_VIGIL_REVISION"]
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(vigil.__file__).resolve().parent, text=True).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision) or actual != revision:
        raise ValueError("Vigil checkout does not match the requested immutable revision")
    app = Vigil.from_config(str(config))
    if not app._input_scanners or app.input_scanner.auto_update:
        raise ValueError("Vigil requires non-empty input scanners and disabled auto-update for independent repetitions")
    result = app.input_scanner.perform_scan(prompt=text)
    if result.get("status") != "success" or result.get("errors"):
        raise ValueError("Official Vigil scan was incomplete: " + str(result.get("errors")))
    findings = []
    for name, scanner in result.get("results", {}).items():
        if name == "scanner:sentiment":
            continue
        for match in scanner.get("matches", []):
            findings.append({"category": "prompt_injection", "confidence": float(match.get("score", 1.0)),
                "detail": json.dumps({"scanner": name, "match": match}, ensure_ascii=False)})
    return {"engine": {"name": "vigil", "revision": actual, "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest()},
        "findings": findings, "official_result": result}


def main() -> int:
    from contextlib import redirect_stdout
    engines = {"llm-guard": _llm_guard, "meta-prompt-guard": _meta_prompt_guard, "vigil": _vigil}
    try:
        with redirect_stdout(sys.stderr):
            result = engines[sys.argv[1]](Path(sys.argv[2]).read_text(encoding="utf-8"))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ImportError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Official engine unavailable or invalid: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
