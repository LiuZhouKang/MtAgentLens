---
status: accepted
date: 2026-10-17
---

# 0002 — Dual `Apache-2.0 OR MIT`, with upstream MIT left untouched

## Context

The tree was legally incoherent:

| Artifact | Said |
|---|---|
| `LICENSE-APACHE` | Apache-2.0 |
| `README.md` | "Apache-2.0 + BPF GPL 例外" |
| 136 of 195 `.rs` files | `SPDX-License-Identifier: MIT` |
| 31 of those 136 | also `Copyright (c) 2026 eunomia-bpf org.` — **upstream-derived** |
| 58 `.rs` files | no header at all |
| `collector/Cargo.toml` | **no** `license` field |
| `analyzers/latency.rs` | **GPL-3.0-or-later** (copyleft, incompatible with the rest) |

## Options

1. **Relicense the 136 MIT files to Apache-2.0.** Rejected: it discards the
   author's repeated in-file statement, and for the 31 upstream files it would be a
   licence violation — MIT code may be redistributed, but its terms may not be
   stripped.
2. **Change the Apache licence to MIT.** Rejected: it rewrites the project's public licence
   to match boilerplate headers, rather than the reverse.
3. **Offer both: `Apache-2.0 OR MIT`.** Chosen. Every existing MIT header stays
   valid (MIT is one of the two offered options), no file is relicensed, and the
   Apache-2.0 statement in `LICENSE-APACHE` remains true. This is the convention used
   across the Rust ecosystem.

## Outcome

- `Cargo.toml`: `license = "Apache-2.0 OR MIT"` (plus `rust-version = "1.85"`, the
  real floor for edition 2024, which was previously absent).
- `LICENSE-MIT` added; the Apache text lives in `LICENSE-APACHE` (the duplicate `LICENSE` file was removed in the 2026-10-19 cleanup).
- 163 project-owned files aligned to the dual header.
- The 31 upstream files keep `MIT` **and their eunomia-bpf copyright** — untouched.
- eBPF files retain their individual SPDX declarations: `process.bpf.c` and
  `stdiocap.bpf.c` use `GPL-2.0 OR BSD-3-Clause`; `tls_capture.bpf.c` and the
  userspace loaders use `LGPL-2.1 OR BSD-2-Clause`. The project dual licence
  does not replace these upstream declarations.
- `latency.rs`'s `GPL-3.0-or-later` header was corrected to the project licence:
  the file carries no third-party attribution and is the project's own
  (currently unregistered) code. **If that file was in fact derived from a GPL-3
  project, this correction is wrong — restore the header and delete the file.**

## Confirmation

`scripts/verify.sh` gate 7: `collector/Cargo.toml` must declare a licence, and
every `.rs` under `collector/src` must carry an SPDX identifier.
