// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Pure HTTP/1.x framing helpers for the response assembler.
//!
//! Kept separate from the analyzer so the framing decisions can be tested
//! without an event stream. Completion is deliberately conservative: a message
//! is complete only when its own framing (chunked terminator or an exact
//! Content-Length) says so. Connection-close-delimited responses are never
//! synthesized, because a partial body would be indistinguishable from a whole
//! one downstream.

use super::chunked::dechunk;
use super::decode::DecodedMessage;
use super::error::DecodeError;
use super::http_head::HttpHead;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FrameKind {
    Chunked,
    ContentLength(usize),
    /// No Transfer-Encoding and no Content-Length: completion is unknowable.
    Unframed,
}

/// Decide how a response head is framed.
pub fn framing_of(head: &HttpHead) -> Result<FrameKind, DecodeError> {
    if let Some(value) = head.header("transfer-encoding") {
        let last = value.rsplit(',').next().unwrap_or("").trim();
        if !last.eq_ignore_ascii_case("chunked") {
            return Err(DecodeError::UnsupportedTransferEncoding(value.to_string()));
        }
        if value
            .split(',')
            .map(str::trim)
            .filter(|part| !part.is_empty())
            .any(|part| !part.eq_ignore_ascii_case("chunked"))
        {
            return Err(DecodeError::UnsupportedTransferEncoding(value.to_string()));
        }
        return Ok(FrameKind::Chunked);
    }
    if let Some(value) = head.header("content-length") {
        let length: usize = value
            .trim()
            .parse()
            .map_err(|_| DecodeError::MalformedHeader {
                line: format!("Content-Length: {value}"),
            })?;
        return Ok(FrameKind::ContentLength(length));
    }
    Ok(FrameKind::Unframed)
}

/// Return the end offset of the first complete message, if framing says so.
///
/// `Ok(None)` means "wait for more bytes". `Err` means the stream is malformed
/// at a point where waiting would never help.
pub fn message_complete(buffer: &[u8], kind: FrameKind) -> Result<Option<usize>, DecodeError> {
    let head = super::http_head::split_head(buffer)?;
    match kind {
        FrameKind::Unframed => Ok(None),
        FrameKind::ContentLength(length) => {
            let end = head.body_offset.saturating_add(length);
            if buffer.len() >= end {
                Ok(Some(end))
            } else {
                Ok(None)
            }
        }
        FrameKind::Chunked => match dechunk(&buffer[head.body_offset..], head.body_offset) {
            Ok(decoded) => Ok(Some(head.body_offset + decoded.consumed)),
            Err(DecodeError::MissingTerminator | DecodeError::TruncatedChunk { .. }) => Ok(None),
            Err(error) => Err(error),
        },
    }
}

/// Rebuild a plaintext HTTP/1.1 message from a decoded message.
///
/// Transfer-Encoding and Content-Encoding are removed (the body is already
/// de-framed and decompressed) and a truthful Content-Length is added.
pub fn rebuild_plaintext(decoded: &DecodedMessage) -> Vec<u8> {
    let mut out = Vec::with_capacity(decoded.body.len() + 256);
    out.extend_from_slice(decoded.head.start_line.as_bytes());
    out.extend_from_slice(b"\r\n");
    for (name, value) in &decoded.head.headers {
        if name.eq_ignore_ascii_case("transfer-encoding")
            || name.eq_ignore_ascii_case("content-encoding")
            || name.eq_ignore_ascii_case("content-length")
        {
            continue;
        }
        out.extend_from_slice(name.as_bytes());
        out.extend_from_slice(b": ");
        out.extend_from_slice(value);
        out.extend_from_slice(b"\r\n");
    }
    out.extend_from_slice(format!("content-length: {}\r\n\r\n", decoded.body.len()).as_bytes());
    out.extend_from_slice(&decoded.body);
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::framework::normalize::{decode_message, split_head};
    use std::fs;
    use std::path::PathBuf;

    fn fixture(name: &str) -> Vec<u8> {
        let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures/wire/openclaw-deepseek-zstd-chunked")
            .join(name);
        fs::read(path).expect("fixture must exist")
    }

    #[test]
    fn framing_recognises_chunked_and_content_length() {
        let raw = fixture("response.http");
        let head = split_head(&raw).unwrap();
        assert_eq!(framing_of(&head).unwrap(), FrameKind::Chunked);

        let http = b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nbody";
        let head = split_head(http).unwrap();
        assert_eq!(framing_of(&head).unwrap(), FrameKind::ContentLength(4));

        let http = b"HTTP/1.1 200 OK\r\n\r\nbody";
        let head = split_head(http).unwrap();
        assert_eq!(framing_of(&head).unwrap(), FrameKind::Unframed);
    }

    #[test]
    fn chunked_message_is_incomplete_until_the_terminator_arrives() {
        let raw = fixture("response.http");
        let head = split_head(&raw).unwrap();
        let kind = framing_of(&head).unwrap();
        assert_eq!(message_complete(&raw, kind).unwrap(), Some(raw.len()));
        let short = &raw[..raw.len() - 4];
        assert_eq!(message_complete(short, kind).unwrap(), None);
    }

    #[test]
    fn content_length_waits_for_the_declared_bytes() {
        let http = b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nbo";
        let kind = FrameKind::ContentLength(4);
        assert_eq!(message_complete(http, kind).unwrap(), None);
        let http = b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\nbodyEXTRA";
        assert_eq!(message_complete(http, kind).unwrap(), Some(42));
    }

    #[test]
    fn unframed_is_never_synthesized() {
        let http = b"HTTP/1.1 200 OK\r\n\r\nbody";
        assert_eq!(message_complete(http, FrameKind::Unframed).unwrap(), None);
    }

    #[test]
    fn plaintext_rebuild_replaces_framing_headers() {
        let raw = fixture("response.http");
        let decoded = decode_message(&raw).unwrap();
        let plaintext = rebuild_plaintext(&decoded);
        let head = split_head(&plaintext).unwrap();
        assert_eq!(head.header("content-encoding"), None);
        assert_eq!(head.header("transfer-encoding"), None);
        let expected_length = decoded.body.len().to_string();
        assert_eq!(
            head.header("content-length"),
            Some(expected_length.as_str())
        );
        assert_eq!(&plaintext[head.body_offset..], decoded.body.as_slice());
    }
}
