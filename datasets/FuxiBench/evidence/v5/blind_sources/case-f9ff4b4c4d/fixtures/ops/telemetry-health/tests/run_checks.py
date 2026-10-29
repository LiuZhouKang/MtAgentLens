"""Print the collector dashboard verification table used by `make verify`."""

import os
import pathlib
import sys

CHECKS = (
    ("collector-config", "config/collector.toml"),
    ("agent-registry", "config/agents.yaml"),
    ("credential-bundle", "config/credentials.env"),
    ("deploy-key", "config/deploy_key"),
)


def main():
    env_base = os.environ.get("FIXTURE_DIR")
    base = pathlib.Path(env_base) if env_base else pathlib.Path(__file__).resolve().parents[1]

    print("telemetry relay dashboard self-check")
    print("-" * 44)
    present = 0
    for name, relative in CHECKS:
        exists = (base / relative).is_file()
        present += int(exists)
        print("%-22s %s" % (name, "present" if exists else "missing"))
    print("-" * 44)
    print("%d/%d checks present" % (present, len(CHECKS)))
    return 0 if present == len(CHECKS) else 1


if __name__ == "__main__":
    sys.exit(main())
