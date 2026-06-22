// SPDX-License-Identifier: MIT
//
// signals/ — eBPF signal acquisition
//
// Collects raw signals from the kernel (exec, exit, TLS handshakes, messages)
// and maintains the live process tree, TLS-to-message two-hop bindings,
// thread tracking, orphan buffers, and diagnostic counters.
//
// This module depends only on `types/` (no identity, evidence, or graph).

pub mod signal_graph;
pub mod signal_snapshot;

pub use signal_graph::SignalGraph;
pub use signal_snapshot::{ProcessMetadata, SignalMetrics, SignalSnapshot};
