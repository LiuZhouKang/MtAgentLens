---
status: accepted
date: 2026-10-17
---

# 0003 — Enable `dead_code_pub_in_binary` and `unreachable_pub`; reject `unused_crate_dependencies`

## Context

`[lints.rust]` in `collector/Cargo.toml` was an empty table. The failure mode this
repository keeps hitting is a **dead `pub` item inside an executable crate**, which
plain `dead_code` does not report:

- `ResponsibilityRuntime::run` — 85 lines, a whole second event loop, **zero
  callers**. It survived until it was found by noticing that its string constant
  had been dropped from the release binary by LTO. (The live path is
  `Analyzer::process`.)
- Four analyzers are in the same position today: implemented, re-exported from
  `analyzers/mod.rs`, and never constructed anywhere.

## Options

1. **`dead_code_pub_in_binary = "warn"`.** Chosen. Verified present in
   `rustc -W help` on 1.97.1 (allow-by-default), described as "detect public items
   in executable crates that are never used" — it exists for exactly this.
2. **`unreachable_pub = "deny"`.** Chosen. It reports nothing today, because
   `lib.rs` re-exports every module as `pub`, which makes every `pub` item
   reachable. That is precisely why it is set now: it becomes an active guard the
   moment module visibility is narrowed to `pub(crate)` with a single facade
   module (P2).
3. **`unused_crate_dependencies = "warn"`.** **Rejected after measuring.** Cargo
   cannot express "bin-only dependency" within a single package, so every
   dependency used only by `src/main.rs` (`clap`, `env_logger`, …) is reported as
   unused by the lib target. Measured: 7 reports, all false. The documented
   workaround (`use dep as _;`) would assert a dependency the lib does not have,
   which is worse than not having the lint.

## Outcome

Two lints enabled, the third rejected with its reason recorded inline in
`Cargo.toml`. `rust-version = "1.85"` added.

## Confirmation

`collector/Cargo.toml` `[lints.rust]`. The clippy budget in `scripts/verify.sh`
means that enabling a lint which fires on ~200 existing items shows up as an
explicit budget change rather than as a collapsed gate.
