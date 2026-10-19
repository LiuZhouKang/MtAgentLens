// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Explicit, machine-readable decode failures.
//!
//! The rest of this repository has a documented failure mode where a parser
//! silently coerces an unknown value into a plausible one (see
//! `responsibility/persistence/reader.rs:140,185`). This module exists so the
//! byte path cannot do that: every malformed input maps to a distinct variant,
//! and no function returns a partially decoded body.

use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DecodeError {
    /// No `\r\n\r\n` found; the message is not complete yet.
    IncompleteHead,
    /// The start line is not an HTTP/1.x request or response line.
    MalformedStartLine,
    /// A header line had no `:` separator or an invalid field name.
    MalformedHeader { line: String },
    /// A chunk-size line was not hexadecimal.
    BadChunkSize { offset: usize, line: String },
    /// A trailer line was malformed.
    MalformedTrailer { offset: usize, line: String },
    /// Chunk data was not followed by CRLF.
    MissingChunkCrlf { offset: usize },
    /// A chunk declared more bytes than the buffer holds.
    TruncatedChunk {
        offset: usize,
        declared: usize,
        available: usize,
    },
    /// The last-chunk `0\r\n\r\n` was never seen.
    MissingTerminator,
    /// A single chunk exceeds the decoder's safety limit.
    ChunkTooLarge { declared: usize, limit: usize },
    /// A decoded body would exceed the decoder's safety limit.
    BodyTooLarge { limit: usize },
    /// `Transfer-Encoding` ended in a coding we cannot safely de-frame.
    UnsupportedTransferEncoding(String),
    /// `Content-Encoding` named a coding we do not implement. We never pass the
    /// bytes through unchanged: that would look like a successful decode.
    UnsupportedContentEncoding(String),
    /// `Content-Length` promised more bytes than are present.
    IncompleteBody { declared: usize, actual: usize },
    /// The decompressor rejected the payload.
    Decompress { encoding: String, message: String },
    /// A zstd frame requests a window larger than the configured maximum.
    WindowTooLarge { window_log: u32, max: u32 },
}

impl fmt::Display for DecodeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::IncompleteHead => write!(f, "incomplete HTTP head (no CRLFCRLF)"),
            Self::MalformedStartLine => write!(f, "malformed HTTP start line"),
            Self::MalformedHeader { line } => write!(f, "malformed header line: {line:?}"),
            Self::BadChunkSize { offset, line } => {
                write!(f, "bad chunk size at offset {offset}: {line:?}")
            }
            Self::MalformedTrailer { offset, line } => {
                write!(f, "malformed trailer at offset {offset}: {line:?}")
            }
            Self::MissingChunkCrlf { offset } => {
                write!(f, "chunk data at offset {offset} is not followed by CRLF")
            }
            Self::TruncatedChunk {
                offset,
                declared,
                available,
            } => write!(
                f,
                "chunk at offset {offset} declares {declared} bytes, {available} available"
            ),
            Self::MissingTerminator => write!(f, "chunked body has no terminating 0 CRLF CRLF"),
            Self::ChunkTooLarge { declared, limit } => {
                write!(f, "chunk of {declared} bytes exceeds limit {limit}")
            }
            Self::BodyTooLarge { limit } => write!(f, "decoded body exceeds limit {limit}"),
            Self::UnsupportedTransferEncoding(value) => {
                write!(f, "unsupported Transfer-Encoding: {value}")
            }
            Self::UnsupportedContentEncoding(value) => {
                write!(f, "unsupported Content-Encoding: {value}")
            }
            Self::IncompleteBody { declared, actual } => {
                write!(
                    f,
                    "Content-Length says {declared} bytes, only {actual} present"
                )
            }
            Self::Decompress { encoding, message } => {
                write!(f, "{encoding} decompression failed: {message}")
            }
            Self::WindowTooLarge { window_log, max } => write!(
                f,
                "compressed frame needs window_log {window_log}, maximum is {max}"
            ),
        }
    }
}

impl std::error::Error for DecodeError {}
