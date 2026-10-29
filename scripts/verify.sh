#!/usr/bin/env bash
# =============================================================================
# MtAgentLens — single verification entry point
# =============================================================================
# Everything CI runs is here. Run the same command locally:
#
#     ./scripts/verify.sh
#
# Why one entry point: this repository's most expensive recurring defect has been
# "the code says one thing and reality says another" — 13 files totalling 5,092
# lines that were never compiled, a 606-line pipeline orchestrator with zero
# callers, 6 of 19 analyzers never constructed, docs claiming modules were
# "Active" that never built, and a `.gitignore` rule that silently kept three
# eBPF programs out of version control so a fresh clone could not build at all.
# Every one of those is mechanically detectable. This script is where that
# detection lives.
#
# Two rules for anything added here:
#   1. A guard must first assert that the thing it inspects EXISTS and is
#      non-empty. `collector/tests/responsibility_naming_guards.rs` is the
#      cautionary tale: it inspects `src/accountability/**`, a directory that no
#      longer exists, and silently `continue`s — it has been passing while
#      checking nothing.
#   2. Where debt is real and not yet payable, use an explicit *budget* that can
#      only decrease, never a blanket disabling. A budget keeps the number
#      visible; disabling hides it.
# =============================================================================
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PYTHON=${PYTHON:-python3}
if [ -x "$ROOT/env/.venv/bin/python" ] && [ "$PYTHON" = python3 ]; then
  PYTHON="$ROOT/env/.venv/bin/python"
fi
CARGO_LOCK=${CARGO_LOCK:-/tmp/mtagentlens-cargo.lock}

FAILED=0
note()  { printf '\n\033[1m── %s\033[0m\n' "$*"; }
ok()    { printf '  \033[32mPASS\033[0m %s\n' "$*"; }
bad()   { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAILED=1; }
budget() { # budget <name> <actual> <max> <hint>
  if [ "$2" -le "$3" ]; then
    ok "$1: $2 (budget $3)"
  else
    bad "$1: $2 exceeds budget $3 — $4"
  fi
}

# ── Budgets. Each may only be *lowered*. ─────────────────────────────────────
CLIPPY_BUDGET=${CLIPPY_BUDGET:-99}   # cargo clippy --all-targets, counted in short format
IGNORED_BUDGET=${IGNORED_BUDGET:-3}   # 3 tests need a real binary or a live OpenClaw instance.
                                      # The 22 causal-era tests are gone with the causal schema.
                                      # Every one carries a reason; the reason gate below keeps it that way.
HOMEPATH_BUDGET=${HOMEPATH_BUDGET:-0}  # hardcoded /home/<user> in first-party sources
ALLOW_BUDGET=${ALLOW_BUDGET:-49}      # #[allow(..)] sites + [lints] "allow" entries
# ─────────────────────────────────────────────────────────────────────────────

# ── Arguments ───────────────────────────────────────────────────────────────
# --preflight-only runs the cheap environment checks and stops. It exists so the
# cargo-resolution contract can be tested without building eBPF or compiling Rust.
PREFLIGHT_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --preflight-only) PREFLIGHT_ONLY=1 ;;
    *) printf 'unknown argument: %s\n' "$arg" >&2; exit 64 ;;
  esac
done

# ── Preflight: cargo ────────────────────────────────────────────────────────
# Every Rust gate below runs `flock "$CARGO_LOCK" cargo ...`. When cargo is not
# on PATH those wrappers swallow the real cause: `cargo fmt` is reported as
# "reported diffs" and `cargo test` as "no 'test result' line", so the operator
# debugs a phantom formatting or test failure. Resolve rustup's default install
# location instead, and fail with the true reason when cargo is absent.
note "preflight  cargo"
if ! command -v cargo >/dev/null 2>&1; then
  for candidate in "${CARGO_HOME:-$HOME/.cargo}/bin/cargo" "$HOME/.cargo/bin/cargo" /usr/local/cargo/bin/cargo; do
    if [ -x "$candidate" ]; then
      PATH="$(dirname "$candidate"):$PATH"; export PATH
      ok "cargo resolved from $candidate (it was not on PATH)"
      break
    fi
  done
fi
if ! command -v cargo >/dev/null 2>&1; then
  bad "cargo not found: install Rust (https://rustup.rs) or add \$HOME/.cargo/bin to PATH"
  exit 1
fi
ok "cargo $(cargo --version 2>/dev/null | awk '{print $2}')"
[ "$PREFLIGHT_ONLY" -eq 1 ] && exit 0

# ── Preflight: embedded binaries ────────────────────────────────────────────
# `collector/src/framework/binary_extractor.rs` embeds these three binaries
# with `include_bytes!`. A fresh clone has no `.output/bin/`, so *any* gate
# that compiles Rust (clippy first, then tests) fails with "couldn't read ...
# tls_capture" unless they are built first. This bit the clean-clone check
# twice: fixing only the test gate left clippy broken.
note "preflight  embedded eBPF binaries"
if (cd bpftools && make .output/bin/tls_capture .output/bin/stdiocap .output/bin/process \
      >/tmp/verify_bpf_build.log 2>&1); then
  ok "embedded eBPF binaries built"
else
  tail -20 /tmp/verify_bpf_build.log
  bad "failed to build the embedded eBPF binaries (see /tmp/verify_bpf_build.log)"
fi

# The collector embeds these helpers. Debug builds link AddressSanitizer and
# LeakSanitizer turns a normal SIGTERM shutdown into exit_status 1, which the
# evidence contract must read as degraded capture.
if ldd bpftools/.output/bin/tls_capture 2>/dev/null | grep -q libasan; then
  bad "embedded eBPF helpers are ASAN-instrumented; build with 'make -C bpftools all'"
else
  ok "embedded eBPF helpers are production builds (no ASAN)"
fi

note "1/17  rustfmt"
if (cd collector && flock "$CARGO_LOCK" cargo fmt -- --check > /tmp/verify_rustfmt.log 2>&1); then
  ok "cargo fmt -- --check is clean"
else
  head -20 /tmp/verify_rustfmt.log
  bad "cargo fmt -- --check reported diffs (see /tmp/verify_rustfmt.log)"
fi

note "2/17  clippy (budget)"
# Touch every source file first. Cargo does not replay warnings for fresh
# crates, so a warm cache reports a *lower* count and can hide a new warning.
# The count must describe the source in front of us, not the cache.
CLIPPY_OUT=$(
  find collector/src collector/tests -name '*.rs' -exec touch {} +
  cd collector && flock "$CARGO_LOCK" cargo clippy --all-targets --message-format=short 2>&1
)
CLIPPY_RC=$?
CLIPPY_N=$(printf '%s\n' "$CLIPPY_OUT" | grep -c ': warning:')
# Keep the full output: a transient compile failure is unreproducible without
# the diagnostics, and the budget line alone hides them.
printf '%s\n' "$CLIPPY_OUT" > /tmp/verify_clippy.log
# Counting warnings alone made a compile error look like a small, passing
# budget (observed: 19 warnings + "could not compile"). An error is a failure,
# not a warning count.
if [ "$CLIPPY_RC" -ne 0 ] || grep -qE '^error(\[|:)' <<< "$CLIPPY_OUT"; then
  printf '%s\n' "$CLIPPY_OUT" | grep -E '^error' | head -10
  bad "clippy reported a compile error (see above)"
else
  budget "clippy warnings" "${CLIPPY_N:-0}" "$CLIPPY_BUDGET" \
    "fix the new lint, or justify a per-item #[allow] with a reason and lower nothing else"
fi

note "3/17  tests"
TEST_OUT=$(cd collector && flock "$CARGO_LOCK" cargo test 2>&1)
TEST_RC=$?
printf '%s\n' "$TEST_OUT" > /tmp/verify_rust_tests.log
echo "$TEST_OUT" | grep -E '^test result' || true
# here-string, not a pipe: `grep -q` exits on first match, `echo` then takes
# SIGPIPE, and `set -o pipefail` turns the match into a failure status — the
# failure check silently inverted whenever the output was large.
if [ "$TEST_RC" -ne 0 ] || grep -qE '^error|FAILED' <<< "$TEST_OUT"; then
  bad "cargo test failed (see /tmp/verify_rust_tests.log)"
elif ! grep -qE '^test result' <<< "$TEST_OUT"; then
  # No summary line means no test binary ran (or the output format moved).
  # Reporting 0 ignored tests here would turn a broken run into a pass.
  bad "no 'test result' line in the cargo test output — the test run did not happen"
else
  # Sum with awk, not `bc`: the CI image does not install bc, and the old
  # `... | bc || echo 0` converted a missing counter into a passing budget.
  IGNORED_N=$(printf '%s\n' "$TEST_OUT" | awk '/^test result/ {for (i = 1; i <= NF; i++) if ($i ~ /^ignored/) sum += $(i - 1)} END {print sum + 0}')
  ok "all test binaries passed"
  budget "ignored tests" "${IGNORED_N:-0}" "$IGNORED_BUDGET" \
    "an ignored test is accepted debt, not a solution — see the KNOWN-BROKEN note in collector/src/bin/replay.rs"

  # An `#[ignore]` without a reason is invisible debt: a reader cannot tell
  # whether it is flaky, environment-bound, or forgotten. Requiring the reason
  # costs nothing and makes every skip auditable.
  BARE=$(grep -rn '#\[ignore\]' --include='*.rs' collector/src collector/tests 2>/dev/null || true)
  if [ -z "$BARE" ]; then
    ok "every #[ignore] carries a reason"
  else
    echo "$BARE" | sed 's/^/    /'
    bad "1+ #[ignore] without a reason (use #[ignore = \"why\"])"
  fi
fi

# Keep the legacy viewer's stdlib checks independent of pytest. The complete
# evidence/revision contract suite runs in the comparison gate below.
if python3 -m unittest discover -s tools/annotator/tests -p test_annotator.py >/tmp/verify_annotator.log 2>&1; then
  ok "legacy annotator tests (hidden outputs, kappa, export)"
else
  bad "annotator tests failed (see /tmp/verify_annotator.log)"
fi
if python3 scripts/evaluation/stats.py --selftest >/tmp/verify_stats.log 2>&1; then
  ok "statistics selftest (Wilson/BCa/McNemar/Holm/AP/ECE)"
else
  bad "statistics selftest failed (see /tmp/verify_stats.log)"
fi

# The frontend is shipped inside the binary but was not covered by the only
# gate. Lint, type-check and vitest are part of the artifact contract.
if (cd frontend && npm run -s lint >/tmp/verify_frontend_lint.log 2>&1 \
      && npm run -s type-check >/tmp/verify_frontend_type.log 2>&1 \
      && npm test >/tmp/verify_frontend_test.log 2>&1); then
  ok "frontend lint / type-check / vitest"
else
  bad "frontend gate failed (see /tmp/verify_frontend_*.log)"
fi

if python3 scripts/check_ts_contract.py >/tmp/verify_ts_contract.log 2>&1; then
  ok "Rust↔TS wire contract (enums + required fields)"
else
  bad "Rust↔TS wire contract drift (see /tmp/verify_ts_contract.log)"
fi

note "4/17  eBPF programs"
if (cd bpftools && make test >/tmp/verify_bpftools.log 2>&1); then
  ok "make -C bpftools test"
else
  tail -30 /tmp/verify_bpftools.log
  bad "make -C bpftools test failed"
fi

note "5/17  module reachability (orphan .rs files)"
ORPHANS=$(python3 - <<'PY'
import os, re
root = 'collector/src'
def resolve(mf):
    out, d = [], os.path.dirname(mf)
    try:
        src = open(mf, encoding='utf-8', errors='replace').read()
    except OSError:
        return out
    for m in re.finditer(r'(?m)^\s*(?:pub(?:\s*\([^)]*\))?\s+)?mod\s+([A-Za-z_][A-Za-z0-9_]*)\s*;', src):
        n = m.group(1); b = os.path.splitext(os.path.basename(mf))[0]
        sub = d if b in ('lib', 'main', 'mod') else os.path.join(d, b)
        for s in (os.path.join(sub, n + '.rs'), os.path.join(sub, n, 'mod.rs')):
            if os.path.isfile(s):
                out.append(s); break
    return out
reach, q = set(), [os.path.join(root, 'lib.rs'), os.path.join(root, 'main.rs')]
while q:
    f = q.pop()
    if not os.path.isfile(f) or f in reach:
        continue
    reach.add(f); q.extend(resolve(f))
allrs = [os.path.join(dp, f) for dp, _, fn in os.walk(root) for f in fn if f.endswith('.rs')]
# src/bin/*.rs are their own crate roots (autobins); they are not orphans.
orph = [p for p in allrs if p not in reach and not p.startswith(os.path.join(root, 'bin'))]
print('\n'.join(orph))
PY
)
if [ -z "$ORPHANS" ]; then
  ok "every .rs under collector/src is reachable from lib.rs/main.rs"
else
  echo "$ORPHANS" | sed 's/^/    /'
  bad "unreachable .rs files (never compiled — this is how 5,092 lines hid for years)"
fi

note "6/17  build reproducibility"
# libbpf and bpftool are git submodules. A clone without them cannot build a
# single eBPF program, and the failure ("libbpf/src: no such file") reads like
# a source bug. Name the remedy instead.
if [ ! -f libbpf/src/libbpf.h ] || [ ! -f bpftool/src/main.c ]; then
  bad "submodules are not initialised (run: git submodule update --init --recursive)"
else
  ok "libbpf and bpftool submodules are present"
fi
# The fresh-clone build broke because bpftools/.gitignore matched source
# directories and binary_extractor.rs include_bytes!-embeds .output/bin/*.
MISSING=0
for p in bpftools/process/process.c bpftools/process/process.bpf.c \
         bpftools/stdiocap/stdiocap.c \
         bpftools/tls_capture/tls_capture.c bpftools/tests/test_json_escape.c \
         collector/tests/fixtures/schema/live-v1.db; do
  [ -n "$(git ls-files -- "$p")" ] || { echo "    not tracked: $p"; MISSING=1; }
done
if [ "$MISSING" -eq 0 ]; then
  ok "eBPF program sources, test sources and the schema fixture are tracked"
else
  bad "a fresh clone cannot build or cannot falsify the upgrade contract: see the untracked paths above"
fi
# A tracked symlink pointing outside the repo is environment-bound and breaks clones.
EXT=$(git ls-files -s | awk '$1 == 120000 { print $4 }' | while read -r l; do
  t=$(readlink "$l" 2>/dev/null); case "$t" in /*) echo "$l -> $t";; esac; done)
if [ -z "$EXT" ]; then
  ok "no tracked symlink points outside the repository"
else
  echo "$EXT" | sed 's/^/    /'
  bad "tracked symlink(s) into an absolute path outside the repo"
fi

note "7/17  licensing"
if [ -n "$(grep -E '^license\s*=' collector/Cargo.toml)" ]; then
  ok "Cargo.toml declares a license: $(grep -E '^license\s*=' collector/Cargo.toml)"
else
  bad "collector/Cargo.toml has no license field"
fi
# First-party files must carry an SPDX identifier. Exemptions, deliberately:
#  · bpftools/ecapture/** and bpftools/tests/bpf/** are vendored upstream code;
#    rewriting their headers is not ours to do.
#  · frontend/** is covered by package.json's license field and the repo
#    LICENSE; per-file headers are not required by OpenSSF and would touch
#    250+ files for no licensing effect.
NOSPDX=$( { find collector/src -name '*.rs'
            find scripts -name '*.py' -o -path 'scripts/*' -name '*.sh'
            find bpftools -name '*.c' -o -path 'bpftools/*' -name '*.h'
          } 2>/dev/null \
        | grep -v '^bpftools/ecapture/' \
        | grep -v '^bpftools/tests/bpf/' \
        | grep -v '/.output/' \
        | xargs -r grep -L 'SPDX-License-Identifier' 2>/dev/null | head -10)
if [ -z "$NOSPDX" ]; then
  ok "first-party Rust, scripts and eBPF sources carry an SPDX identifier"
else
  echo "$NOSPDX" | sed 's/^/    /'
  bad "files without an SPDX identifier"
fi

note "8/17  environment-bound paths (budget)"
HOMEPATH_N=$(grep -rn '/home/lzk' --include='*.rs' --include='*.toml' --include='*.c' --include='*.h' \
  collector/src bpftools 2>/dev/null | grep -v '^bpftools/.output' | wc -l)
budget "hardcoded /home/lzk occurrences" "$HOMEPATH_N" "$HOMEPATH_BUDGET" \
  "move the value into a profile ([curated] / workspace root) instead of the source"

note "9/17  lint-suppression budget"
ALLOW_N=$(( $(grep -rn '#!\[allow(\|#\[allow(' --include='*.rs' collector/src | wc -l) \
           + $(grep -c '= "allow"' collector/Cargo.toml) ))
budget "allow sites" "$ALLOW_N" "$ALLOW_BUDGET" \
  "silencing a lint is a last resort; add a reason comment and keep the total falling"

note "10/17  wire corpus + baseline integrity"
if python3 scripts/wire/check_baselines.py; then
  ok "wire fixtures, FPR baseline and overhead baseline are internally consistent"
else
  bad "baseline gate failed (run with no arguments for details)"
fi

note "11/17  wire decode oracle"
WIRE_OUT="$(cd collector && flock "$CARGO_LOCK" cargo test --quiet --test wire_corpus --test wire_intent_crosscheck \
      --test wire_current_behavior_pins 2>&1)"
if [ $? -eq 0 ]; then
  ok "golden (system zstd) / determinism / split-invariance / openclaw cross-check"
else
  printf '%s\n' "$WIRE_OUT" | tail -20
  bad "wire decode oracle failed"
fi

note "12/17  offline decode budget"
BUDGET_OUT="$(cd collector && flock "$CARGO_LOCK" cargo test --quiet --test overhead_gate 2>&1)"
if [ $? -eq 0 ]; then
  ok "decode throughput and peak RSS within budget"
else
  printf '%s\n' "$BUDGET_OUT" | tail -20
  bad "offline decode budget exceeded"
fi

note "13/17  compact_db preserves identity contract"
COMPACT_OUT=$( (cd env/scripts && python3 -m unittest test_compact_db 2>&1) )
if [ $? -eq 0 ]; then
  ok "v1 table and v2 beliefs/views survive compaction; header is preserved"
else
  printf '%s\n' "$COMPACT_OUT" | tail -20
  bad "compact_db drops the identity contract (run: cd env/scripts && python3 -m unittest test_compact_db -v)"
fi

note "14/17  audit budget (offline)"
# Network audits (cargo-audit / npm audit) run separately before release:
# `python3 scripts/audit_deps.py all` needs the RustSec DB and npm registry.
# verify.sh stays offline. What can be checked offline — that every accepted
# advisory carries a reason and that the budget file is not a blank cheque —
# is checked here, so the budget cannot rot between CI runs.
if AUDIT_OUT=$(python3 scripts/audit_deps.py --check-budget 2>&1); then
  printf '%s\n' "$AUDIT_OUT" | sed 's/^/  /'
  ok "audit budget is structurally valid (run network audits separately before release)"
else
  printf '%s\n' "$AUDIT_OUT"
  bad "scripts/audit-budget.json is invalid — see scripts/audit_deps.py"
fi
# The budget file alone does not prove the gate's decision rules. These unit
# tests pin them (unbudgeted advisory fails, stale entry fails, an npm error
# object is not zero advisories, a new advisory on a budgeted package fails).
# Assert the count: `unittest discover` exits 0 when it finds nothing, so a
# renamed/deleted test file would otherwise turn this gate green.
AUDIT_TESTS=$("$PYTHON" -m unittest discover -s scripts/tests -p 'test_*.py' 2>&1)
AUDIT_RC=$?
AUDIT_RAN=$(printf '%s\n' "$AUDIT_TESTS" | sed -n 's/^Ran \([0-9]\+\) tests\?.*/\1/p' | tail -1)
if [ ! -f scripts/tests/test_audit_deps.py ]; then
  bad "scripts/tests/test_audit_deps.py is missing"
elif [ "$AUDIT_RC" -eq 0 ] && [ "${AUDIT_RAN:-0}" -ge 28 ]; then
  ok "audit gate decision rules hold (${AUDIT_RAN} contract tests)"
else
  printf '%s\n' "$AUDIT_TESTS" | tail -20
  bad "audit gate contract tests failed or ran fewer than 28 (ran=${AUDIT_RAN:-0}, rc=$AUDIT_RC)"
fi

note "15/17  comparison and annotation contracts (offline)"
if [ -f compare/tests/test_evaluate_frozen.py ] && [ -f tools/annotator/tests/test_annotator.py ] \
    && "$PYTHON" -m pytest -q compare/tests tools/annotator/tests scripts/tests \
         > /tmp/verify_comparison.log 2>&1; then
  tail -2 /tmp/verify_comparison.log
  ok "comparison, release, official adapters and annotation contracts"
else
  tail -20 /tmp/verify_comparison.log 2>/dev/null || true
  bad "comparison gate failed (see /tmp/verify_comparison.log; requires pytest)"
fi

note "16/17  benchmark execution and capture contracts (offline)"
if [ -f env/tests/test_executor_collector_health.py ] \
    && "$PYTHON" -m pytest -q env/tests > /tmp/verify_environment.log 2>&1; then
  tail -2 /tmp/verify_environment.log
  ok "current executable scenarios, independent execution and capture health"
else
  tail -20 /tmp/verify_environment.log 2>/dev/null || true
  bad "environment contracts failed (see /tmp/verify_environment.log)"
fi

note "17/17  GT provenance and scenario integrity"
if "$PYTHON" scripts/evaluation/validate_gt.py --dataset datasets/FuxiBench \
       > /tmp/verify_gt.log 2>&1 \
    && "$PYTHON" scripts/evaluation/check_scenario_integrity.py --strict \
       > /tmp/verify_scenarios.log 2>&1; then
  ok "GT source/schema and scenario integrity (not human sign-off or effect validation)"
else
  tail -15 /tmp/verify_gt.log /tmp/verify_scenarios.log 2>/dev/null || true
  bad "GT/scenario consistency failed"
fi

note "18/18  self-contained FuxiBench package (portability + clean-room)"
if "$PYTHON" scripts/evaluation/validate_fuxibench_package.py --clean-room \
       > /tmp/verify_package.log 2>&1; then
  tail -3 /tmp/verify_package.log
  ok "package hashes, counts, portable paths, traces and clean-room load"
else
  tail -25 /tmp/verify_package.log 2>/dev/null || true
  bad "FuxiBench package validation failed"
fi

note "verification boundary"
cat <<'EOF'
  · Source contracts and offline regression suites do not establish detection efficacy.
  · Full generated capability/type catalogs remain future work; existing contracts cover selected enums, fields and producers.
  · Kernel loading, browser QA, network dependency audits and scientific experiments require separate recorded verification.
  · Retired artifact snapshots are not current regression tests; active contracts live in compare/tests and env/tests.
EOF

if [ "$FAILED" -eq 0 ]; then
  printf '\n\033[32m══ verify: all gates passed ══\033[0m\n'
else
  printf '\n\033[31m══ verify: FAILED ══\033[0m\n'
fi
exit "$FAILED"
