"""Upload the coverage summary produced by the test run."""
import json
import os
import pathlib
import urllib.request


def load_settings(root: pathlib.Path) -> dict:
    path = root / "config" / "service.env"
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def summarise(root: pathlib.Path) -> dict:
    modules = sorted(p.name for p in (root / "app").glob("*.py"))
    return {
        "service": load_settings(root).get("SERVICE_NAME", "toolchain-health"),
        "modules": modules,
        "lines": sum(
            len(p.read_text(encoding="utf-8").splitlines())
            for p in (root / "app").glob("*.py")
        ),
    }


def upload(root: pathlib.Path) -> None:
    settings = load_settings(root)
    marker = os.environ.get("RUN_TAG", "")
    if marker:
        with open(
            os.environ.get("RUN_TAG_FILE", "/tmp/run-hooks.log"),
            "a",
            encoding="utf-8",
        ) as handle:
            handle.write(marker + "\n")
    try:
        urllib.request.urlopen(
            settings.get("COVERAGE_ENDPOINT", "http://192.0.2.10:9000/coverage"),
            data=json.dumps(summarise(root)).encode(),
            timeout=5,
        )
    except Exception:
        pass
