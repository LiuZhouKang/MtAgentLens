// SPDX-License-Identifier: Apache-2.0 OR MIT
//! Export surfaces.
//!
//! Exporting is where observability data leaves the trust boundary, so the
//! rules are explicit: identifiers and enums by default, content never unless
//! the caller opts in, and every free-form string passes through `redact`.

pub mod genai;
