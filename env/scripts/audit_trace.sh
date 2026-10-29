#!/usr/bin/env bash
# 单场景 trace 质量审计。Exit: 0 pass / 2 blocked / 3 invalid input.
# Usage: ./audit_trace.sh <run-dir> <scenario-id> [--sources tls,process,stdio]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$SCRIPT_DIR/audit_trace.py" "$@"
