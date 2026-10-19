// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 eunomia-bpf org.

pub mod analyzers;
pub mod binary_extractor;
pub mod core;
pub mod normalize;
pub mod runners;

// Re-export commonly used types for convenience
// Note: These may show as unused in main.rs but they're exported for external use
#[allow(unused_imports)]
pub use analyzers::{Analyzer, OutputAnalyzer};
#[allow(unused_imports)]
pub use binary_extractor::BinaryExtractor;
#[allow(unused_imports)]
pub use core::Event;
#[allow(unused_imports)]
pub use runners::{
    EventStream, FakeRunner, ProcessRunner, Runner, RunnerError, SslRunner, StdioRunner,
};
