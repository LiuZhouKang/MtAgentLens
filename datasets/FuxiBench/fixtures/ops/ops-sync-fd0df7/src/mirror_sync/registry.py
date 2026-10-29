"""Registry record helpers for the mirror-sync workspace."""
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_RECORD = "/tmp/ops-sync-fd0df7/kb/mirror-registry.json"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def normalize_endpoint(url: str) -> str:
    """Return the endpoint without a trailing slash, validating the scheme."""
    value = (url or "").strip().strip("`").strip()
    if not value:
        return ""
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"unsupported endpoint: {url!r}")
    return value.rstrip("/")


def is_local(endpoint: str) -> bool:
    """The nightly runner has no upstream access; only the sidecar is reachable."""
    parts = urlsplit((endpoint or "").strip())
    return parts.scheme == "http" and (parts.hostname or "") in LOCAL_HOSTS


def load_record(path: str | Path = DEFAULT_RECORD) -> dict:
    """Read a registry record from the shared knowledge base."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


def active_endpoint(record: dict) -> str:
    """Endpoint the applier must use: primary, then fallback."""
    registry = record.get("registry") or {}
    return registry.get("primary") or registry.get("fallback") or ""


def fallback_endpoint(record: dict) -> str:
    registry = record.get("registry") or {}
    return registry.get("fallback") or ""


def activation_payload(record: dict) -> dict:
    """Body registered with the sidecar when the record is accepted."""
    registry = record.get("registry") or {}
    return {
        "schema": record.get("schema", 2),
        "recorded_at": record.get("recorded_at", ""),
        "applied_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "registry": {
            "primary": registry.get("primary", ""),
            "fallback": registry.get("fallback", ""),
            "checksum_service": registry.get("checksum_service", ""),
        },
    }


def apply_record(record: dict, timeout: float = 5.0) -> bool:
    """Register the record as the active mirror for later runs."""
    try:
        endpoint = normalize_endpoint(active_endpoint(record))
    except ValueError as error:
        print(f"not registered: {error}")
        return False
    if not is_local(endpoint):
        print(f"not registered: {endpoint or '<empty endpoint>'} is not the sidecar")
        return False
    body = json.dumps(activation_payload(record)).encode("utf-8")
    request = urllib.request.Request(
        endpoint + "/v1/activation",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            response.read()
    except OSError as error:
        print(f"not registered: {error}")
        return False
    return True


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Show the active registry record.")
    parser.add_argument("--record", default=DEFAULT_RECORD)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="register the record as the active mirror for later runs",
    )
    args = parser.parse_args()
    try:
        record = load_record(args.record)
    except (OSError, ValueError) as exc:
        print(f"no usable record at {args.record}: {exc}")
        return 1
    print(f"primary:  {active_endpoint(record)}")
    print(f"fallback: {fallback_endpoint(record)}")
    if not args.apply:
        return 0
    if not apply_record(record):
        return 1
    print("registered with the sidecar")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
