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
CLIPPY_BUDGET=${CLIPPY_BUDGET:-167}   # cargo clippy --all-targets, counted in short format
IGNORED_BUDGET=${IGNORED_BUDGET:-3}   # 3 tests need a real binary or a live OpenClaw instance.
                                      # The 22 causal-era tests are gone with the causal schema.
                                      # Every one carries a reason; the reason gate below keeps it that way.
HOMEPATH_BUDGET=${HOMEPATH_BUDGET:-0}  # hardcoded /home/<user> in first-party sources
ALLOW_BUDGET=${ALLOW_BUDGET:-49}      # #[allow(..)] sites + [lints] "allow" entries
# ─────────────────────────────────────────────────────────────────────────────

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

note "1/14  rustfmt"
if [ -z "$(cd collector && cargo fmt -- --check 2>&1)" ]; then
  ok "cargo fmt -- --check is clean"
else
  (cd collector && cargo fmt -- --check 2>&1 | head -20)
  bad "cargo fmt -- --check reported diffs (run: cd collector && cargo fmt)"
fi

note "2/14  clippy (budget)"
# Touch every source file first. Cargo does not replay warnings for fresh
# crates, so a warm cache reports a *lower* count and can hide a new warning.
# The count must describe the source in front of us, not the cache.
CLIPPY_OUT=$( (
  find collector/src collector/tests -name '*.rs' -exec touch {} +
  cd collector && cargo clippy --all-targets --message-format=short 2>&1
) || true)
CLIPPY_N=$(printf '%s\n' "$CLIPPY_OUT" | grep -c ': warning:')
# Keep the full output: a transient compile failure is unreproducible without
# the diagnostics, and the budget line alone hides them.
printf '%s\n' "$CLIPPY_OUT" > /tmp/verify_clippy.log
# Counting warnings alone made a compile error look like a small, passing
# budget (observed: 19 warnings + "could not compile"). An error is a failure,
# not a warning count.
if grep -qE '^error(\[|:)' <<< "$CLIPPY_OUT"; then
  printf '%s\n' "$CLIPPY_OUT" | grep -E '^error' | head -10
  bad "clippy reported a compile error (see above)"
else
  budget "clippy warnings" "${CLIPPY_N:-0}" "$CLIPPY_BUDGET" \
    "fix the new lint, or justify a per-item #[allow] with a reason and lower nothing else"
fi

note "3/14  tests"
TEST_OUT=$( (cd collector && cargo test 2>&1) || true)
echo "$TEST_OUT" | grep -E '^test result' || true
# here-string, not a pipe: `grep -q` exits on first match, `echo` then takes
# SIGPIPE, and `set -o pipefail` turns the match into a failure status — the
# failure check silently inverted whenever the output was large.
if grep -qE '^error|FAILED' <<< "$TEST_OUT"; then
  bad "cargo test failed (see the output above)"
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

note "4/14  eBPF programs"
if (cd bpftools && make test >/tmp/verify_bpftools.log 2>&1); then
  ok "make -C bpftools test"
else
  tail -30 /tmp/verify_bpftools.log
  bad "make -C bpftools test failed"
fi

note "5/14  module reachability (orphan .rs files)"
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

note "6/14  build reproducibility"
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

note "7/14  licensing"
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

note "8/14  environment-bound paths (budget)"
HOMEPATH_N=$(grep -rn '/home/lzk' --include='*.rs' --include='*.toml' --include='*.c' --include='*.h' \
  collector/src bpftools 2>/dev/null | grep -v '^bpftools/.output' | wc -l)
budget "hardcoded /home/lzk occurrences" "$HOMEPATH_N" "$HOMEPATH_BUDGET" \
  "move the value into a profile ([curated] / workspace root) instead of the source"

note "9/14  lint-suppression budget"
ALLOW_N=$(( $(grep -rn '#!\[allow(\|#\[allow(' --include='*.rs' collector/src | wc -l) \
           + $(grep -c '= "allow"' collector/Cargo.toml) ))
budget "allow sites" "$ALLOW_N" "$ALLOW_BUDGET" \
  "silencing a lint is a last resort; add a reason comment and keep the total falling"

note "10/14  wire corpus + baseline integrity"
if python3 scripts/wire/check_baselines.py; then
  ok "wire fixtures, FPR baseline and overhead baseline are internally consistent"
else
  bad "baseline gate failed (run with no arguments for details)"
fi

note "11/14  wire decode oracle"
WIRE_OUT="$(cd collector && cargo test --quiet --test wire_corpus --test wire_intent_crosscheck \
      --test wire_current_behavior_pins 2>&1)"
if [ $? -eq 0 ]; then
  ok "golden (system zstd) / determinism / split-invariance / openclaw cross-check"
else
  printf '%s\n' "$WIRE_OUT" | tail -20
  bad "wire decode oracle failed"
fi

note "12/14  offline decode budget"
BUDGET_OUT="$(cd collector && cargo test --quiet --test overhead_gate 2>&1)"
if [ $? -eq 0 ]; then
  ok "decode throughput and peak RSS within budget"
else
  printf '%s\n' "$BUDGET_OUT" | tail -20
  bad "offline decode budget exceeded"
fi

note "13/14  compact_db preserves identity contract"
COMPACT_OUT=$( (cd env/scripts && python3 -m unittest test_compact_db 2>&1) )
if [ $? -eq 0 ]; then
  ok "v1 table and v2 beliefs/views survive compaction; header is preserved"
else
  printf '%s\n' "$COMPACT_OUT" | tail -20
  bad "compact_db drops the identity contract (run: cd env/scripts && python3 -m unittest test_compact_db -v)"
fi

note "14/14  audit budget (offline)"
# The network audits (cargo-audit / npm audit) live in the CI `audit` stage:
# they need the RustSec database and the npm registry, and verify.sh stays
# offline and deterministic. What *can* be checked offline — that every accepted
# advisory carries a reason and that the budget file is not a blank cheque —
# is checked here, so the budget cannot rot between CI runs.
if AUDIT_OUT=$(python3 scripts/audit_deps.py --check-budget 2>&1); then
  printf '%s\n' "$AUDIT_OUT" | sed 's/^/  /'
  ok "audit budget is structurally valid (network audits run in the CI audit stage)"
else
  printf '%s\n' "$AUDIT_OUT"
  bad "scripts/audit-budget.json is invalid — see scripts/audit_deps.py"
fi
# The budget file alone does not prove the gate's decision rules. These unit
# tests pin them (unbudgeted advisory fails, stale entry fails, an npm error
# object is not zero advisories, a new advisory on a budgeted package fails).
# Assert the count: `unittest discover` exits 0 when it finds nothing, so a
# renamed/deleted test file would otherwise turn this gate green.
AUDIT_TESTS=$(python3 -m unittest discover -s scripts/tests -p 'test_*.py' 2>&1)
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

note "deferred gates (tracked, not yet implemented)"
cat <<'EOF'
  · capability manifest ↔ real wiring (bidirectional assertion)   — needs collector/src/catalog.rs (P2)
  · generated artifacts up to date (cargo xtask gen + git diff)    — needs the generator (P2)
  · produces-without-producer (unbacked stage) check               — needs the catalog (P2)
  · Rust↔TypeScript contract drift                                 — needs type generation (P2)
EOF

if [ "$FAILED" -eq 0 ]; then
  printf '\n\033[32m══ verify: all gates passed ══\033[0m\n'
else
  printf '\n\033[31m══ verify: FAILED ══\033[0m\n'
fi
exit "$FAILED"
