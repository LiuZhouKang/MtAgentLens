// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Byte-exact HTTP payload normalization.
//!
//! The existing capture path is lossy in two independent places:
//!   1. `bpftools/tls_capture/tls_capture.c:589` escapes non-printable bytes as
//!      `\u00XX`, which is injective only if decoded as *bytes* — `serde_json`
//!      turns `é` into two UTF-8 bytes and destroys exactly one byte.
//!   2. `framework/runners/common.rs:223` runs `String::from_utf8_lossy` on the
//!      line, so any invalid sequence becomes U+FFFD before anything else sees
//!      it.
//!
//! This module is the byte-preserving replacement for the decode half of that
//! path. P0.5 used it as the object under test for the golden / determinism /
//! split-invariance gates; P1 wires it into the runner and the response chain.

pub mod assembler;
pub mod chunked;
pub mod coding;
pub mod decode;
pub mod error;
pub mod http_head;
pub mod transport;

pub use assembler::{FrameKind, framing_of, message_complete, rebuild_plaintext};
pub use chunked::{Dechunked, dechunk};
pub use coding::{decode_content, parse_codings};
pub use decode::{DecodedMessage, decode_message};
pub use error::DecodeError;
pub use http_head::{HttpHead, split_head};
pub use transport::{
    PayloadSource, TransportError, WirePayload, ensure_data_b64, legacy_data_bytes, payload_bytes,
};
