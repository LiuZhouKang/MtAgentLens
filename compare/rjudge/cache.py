"""Content-addressed cache for judge responses.

The cache key is derived from everything that can change the answer:
the protocol step, model, decoding parameters, the trace content hash, the
prompt bundle hash, and the exact messages sent. Nothing that does *not* change
the answer (trace id, run path, timestamps) is part of the key, so identical
inputs are never re-billed and cannot silently drift.

Entries are plain JSON files (``<root>/<first2>/<sha256>.json``) so a cached
response is auditable after the fact: it records what was asked and what came
back. A corrupt entry is treated as a miss, never as a cached answer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .trace_export import canonical_json, sha256_text

CACHE_SCHEMA = "mtagentlens.rjudge_cache.v1"
_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


class ResponseCache:
    """Filesystem cache of raw judge responses."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    # -- keys --------------------------------------------------------------
    @staticmethod
    def make_key(
        *,
        step: str,
        model: str,
        temperature: float,
        seed: int | None,
        max_tokens: int,
        trace_sha256: str,
        prompt_sha256: str,
        messages_sha256: str,
    ) -> str:
        return sha256_text(
            canonical_json(
                {
                    "schema": CACHE_SCHEMA,
                    "step": step,
                    "model": model,
                    "temperature": temperature,
                    "seed": seed,
                    "max_tokens": max_tokens,
                    "trace_sha256": trace_sha256,
                    "prompt_sha256": prompt_sha256,
                    "messages_sha256": messages_sha256,
                }
            )
        )

    def path_for(self, key: str) -> Path:
        if not _KEY_RE.match(key or ""):
            raise ValueError(f"cache key must be a sha256 hex digest, got {key!r}")
        return self.root / key[:2] / f"{key}.json"

    # -- access ------------------------------------------------------------
    def get(self, key: str) -> dict[str, Any] | None:
        """Return the cached entry, or ``None`` on a miss/corrupt entry."""
        path = self.path_for(key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("schema") != CACHE_SCHEMA:
            return None
        if not isinstance(payload.get("response"), str):
            return None
        if not isinstance(payload.get("meta"), dict):
            return None
        return payload

    def put(self, key: str, *, response: str, meta: Mapping[str, Any]) -> Path:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": CACHE_SCHEMA,
            "key": key,
            "response": response,
            "meta": dict(meta),
        }
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        return path

    # -- introspection -----------------------------------------------------
    def __len__(self) -> int:
        if not self.root.is_dir():
            return 0
        return sum(1 for _ in self.root.rglob("*.json"))

    def describe(self) -> dict[str, Any]:
        return {"schema": CACHE_SCHEMA, "root": str(self.root), "entries": len(self)}

    def invalidate(self, key: str) -> bool:
        path = self.path_for(key)
        try:
            path.unlink()
        except OSError:
            return False
        return True
