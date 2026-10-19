// SPDX-License-Identifier: Apache-2.0 OR MIT
//
// identity/ — Agent identity resolution (Phase 1 + Phase 2).
//
// API:
//   - IdentityResolver — Phase 1 provisional + Phase 2 authoritative resolution
//   - IdentityProvider — trait for graph/ to look up agent assignments
//   - union_find::* — helper functions: is_agent_process, is_subagent_boundary, etc.

pub mod resolver;
pub mod union_find;

// ── API exports ─────────────────────────────────────────────────────
pub use resolver::{IdentityProvider, IdentityResolver};
pub use union_find::AuthoritativeResolution;

// Re-export types that moved to types/ (consumers still import from identity/)
pub use crate::responsibility::types::{UnresolvedDiagnostic, UnresolvedReason};
