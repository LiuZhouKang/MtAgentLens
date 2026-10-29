// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project
//
// provenance/ — OS-observable content provenance.
//
// The monitor reconstructs where the content behind an action came from:
// user-authored instructions, retrieved file/network content, or model output
// derived from either. A boundary crossing is only actionable when untrusted,
// unrequested content reaches an action the user did not authorize. This is
// the OS-layer counterpart of the integrity/confidentiality labels used by
// CaMeL and Fides at the application layer.

pub mod envelope;
pub mod labels;
pub mod span_store;

pub use envelope::{CapabilityEnvelope, CapabilityVerdict};
pub use labels::{Authorization, Confidentiality, Integrity, Origin, ProvenanceTag};
pub use span_store::{ContentSpan, MatchPolicy, SpanMatch, SpanStore};
