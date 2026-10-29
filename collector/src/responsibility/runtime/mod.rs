// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

// Runtime modules (spec §15).
pub mod buffer;
pub mod context_witness;
pub mod provenance_tracker;
mod recording_context;
pub mod routing;
pub mod runtime;

// ── Re-exports ─────────────────────────────────────────────────────────────
pub use buffer::GraphDeltaBuffer;
pub use routing::dispatch_event;
pub use runtime::{ResponsibilityRuntime, RuntimeConfig};
