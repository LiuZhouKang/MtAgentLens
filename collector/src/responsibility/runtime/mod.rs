// Runtime modules (spec §15).
pub mod buffer;
pub mod routing;
pub mod runtime;

// ── Re-exports ─────────────────────────────────────────────────────────────
pub use buffer::GraphDeltaBuffer;
pub use routing::dispatch_event;
pub use runtime::{
    ResponsibilityRuntime, RuntimeConfig, SessionEndSignal,
};
