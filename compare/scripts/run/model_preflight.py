# SPDX-License-Identifier: Apache-2.0 OR MIT
"""Pre-campaign model preflight.

A campaign must not start when the vendor will reject every run. The failure is
observable with a single one-token request, so paying for the request is cheaper
than discovering the problem after the first (or nineteenth) scenario.

Exit codes: 0 = usable, 3 = campaign must not start, 4 = preflight unavailable
(the caller may decide to continue; the run-level fail-fast still applies).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCHEMA = "mtagentlens.model-preflight/1"
PROBE_PROMPT = "ok"
PROBE_MAX_TOKENS = 1


def classify(status: int, body: str) -> str:
    """Map an HTTP probe result to a campaign decision."""
    lowered = body.lower()
    if status == 200:
        return "ok"
    if "insufficient" in lowered and "balance" in lowered:
        return "insufficient_balance"
    if status == 402 or "insufficient_quota" in lowered or "credit balance" in lowered:
        return "insufficient_balance"
    if status in (401, 403):
        return "unauthorized"
    return "error"


def probe(base_url: str, token: str, model: str, *, timeout: float = 30.0) -> dict:
    """One one-token message request against an Anthropic-compatible endpoint."""
    url = f"{base_url.rstrip('/')}/v1/messages"
    payload = json.dumps({
        "model": model,
        "max_tokens": PROBE_MAX_TOKENS,
        "messages": [{"role": "user", "content": PROBE_PROMPT}],
    }).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "content-type": "application/json",
            "x-api-key": token,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(4096).decode("utf-8", "replace")
            return {"schema": SCHEMA, "status": response.status,
                    "decision": classify(response.status, body), "detail": ""}
    except urllib.error.HTTPError as error:
        body = error.read(4096).decode("utf-8", "replace")
        return {"schema": SCHEMA, "status": error.code,
                "decision": classify(error.code, body), "detail": body[:400]}
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return {"schema": SCHEMA, "status": None,
                "decision": "unavailable", "detail": str(error)[:400]}


def blocks_campaign(result: dict) -> bool:
    """Whether a preflight result means "do not start" (a definite vendor rejection)."""
    return result.get("decision") in {"insufficient_balance", "unauthorized"}


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--env-file", type=Path, default=None,
                        help="fallback source for the three values (e.g. env/config/claude.env)")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    values: dict[str, str] = {}
    if args.env_file and args.env_file.is_file():
        values = read_env_file(args.env_file)
    base_url = args.base_url or values.get("ANTHROPIC_BASE_URL", "")
    token = args.token or values.get("ANTHROPIC_AUTH_TOKEN") or values.get("ANTHROPIC_API_KEY", "")
    model = args.model or values.get("ANTHROPIC_MODEL") or values.get("MODEL", "")
    if not base_url or not token or not model:
        result = {"schema": SCHEMA, "status": None, "decision": "unavailable",
                  "detail": "base URL, token or model missing"}
    else:
        result = probe(base_url, token, model, timeout=args.timeout)
    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if blocks_campaign(result):
        return 3
    return 0 if result["decision"] == "ok" else 4


if __name__ == "__main__":
    raise SystemExit(main())
