// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Strict RFC 9112 §7.1 chunked-transfer decoding over bytes.
//!
//! The existing string-based decoder in
//! `responsibility/evidence/intent_extractor.rs:61` stops at the first bad
//! chunk-size line and returns whatever it accumulated. That is a silent
//! truncation. This decoder returns an error instead, and it never assumes the
//! body is UTF-8.

use super::error::DecodeError;

/// Largest single chunk we will accept (64 MiB). A hostile or corrupt size line
/// must not be allowed to reserve memory before its bytes have arrived.
pub const MAX_CHUNK_BYTES: usize = 64 * 1024 * 1024;
/// Largest assembled body we will accept.
pub const MAX_BODY_BYTES: usize = 64 * 1024 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Dechunked {
    pub body: Vec<u8>,
    pub trailers: Vec<(String, Vec<u8>)>,
    /// Bytes consumed from the start of the input, including the final CRLF.
    pub consumed: usize,
}

/// Decode a chunked body. `base_offset` is added to error offsets so callers
/// can report a position in the original capture.
pub fn dechunk(body: &[u8], base_offset: usize) -> Result<Dechunked, DecodeError> {
    let mut pos = 0usize;
    let mut out: Vec<u8> = Vec::new();

    loop {
        let line_end = find_crlf(body, pos).ok_or(DecodeError::MissingTerminator)?;
        let size_field = body[pos..line_end]
            .split(|&b| b == b';')
            .next()
            .unwrap_or(&[]);
        let size_field = trim_ows(size_field);

        let size = parse_hex(size_field).ok_or_else(|| DecodeError::BadChunkSize {
            offset: base_offset + pos,
            line: String::from_utf8_lossy(size_field).into_owned(),
        })?;
        pos = line_end + 2;

        if size == 0 {
            let trailers = read_trailers(body, &mut pos, base_offset)?;
            return Ok(Dechunked {
                body: out,
                trailers,
                consumed: pos,
            });
        }

        if size > MAX_CHUNK_BYTES {
            return Err(DecodeError::ChunkTooLarge {
                declared: size,
                limit: MAX_CHUNK_BYTES,
            });
        }
        if out.len().saturating_add(size) > MAX_BODY_BYTES {
            return Err(DecodeError::BodyTooLarge {
                limit: MAX_BODY_BYTES,
            });
        }
        if pos.saturating_add(size) > body.len() {
            return Err(DecodeError::TruncatedChunk {
                offset: base_offset + pos,
                declared: size,
                available: body.len().saturating_sub(pos),
            });
        }

        out.extend_from_slice(&body[pos..pos + size]);
        pos += size;

        // The trailing CRLF may simply not have arrived yet. Distinguishing
        // "incomplete" from "malformed" matters: the assembler retries the
        // former and gives up on the latter. A live capture delivers exactly
        // this shape (chunk data at the end of one SSL_read, CRLF in the next).
        if pos + 2 > body.len() {
            return Err(DecodeError::MissingTerminator);
        }
        if &body[pos..pos + 2] != b"\r\n" {
            return Err(DecodeError::MissingChunkCrlf {
                offset: base_offset + pos,
            });
        }
        pos += 2;
    }
}

fn read_trailers(
    body: &[u8],
    pos: &mut usize,
    base_offset: usize,
) -> Result<Vec<(String, Vec<u8>)>, DecodeError> {
    let mut trailers = Vec::new();
    loop {
        let line_end = find_crlf(body, *pos).ok_or(DecodeError::MissingTerminator)?;
        if line_end == *pos {
            *pos = line_end + 2;
            return Ok(trailers);
        }
        let line = &body[*pos..line_end];
        let colon =
            line.iter()
                .position(|&b| b == b':')
                .ok_or_else(|| DecodeError::MalformedTrailer {
                    offset: base_offset + *pos,
                    line: String::from_utf8_lossy(line).into_owned(),
                })?;
        let name = String::from_utf8_lossy(&line[..colon]).trim().to_string();
        let value = trim_ows(&line[colon + 1..]).to_vec();
        if name.is_empty() {
            return Err(DecodeError::MalformedTrailer {
                offset: base_offset + *pos,
                line: String::from_utf8_lossy(line).into_owned(),
            });
        }
        trailers.push((name, value));
        *pos = line_end + 2;
    }
}

fn find_crlf(body: &[u8], from: usize) -> Option<usize> {
    if from > body.len() {
        return None;
    }
    body[from..]
        .windows(2)
        .position(|window| window == b"\r\n")
        .map(|offset| from + offset)
}

fn trim_ows(mut bytes: &[u8]) -> &[u8] {
    while let [first, rest @ ..] = bytes {
        if *first == b' ' || *first == b'\t' {
            bytes = rest;
        } else {
            break;
        }
    }
    while let [rest @ .., last] = bytes {
        if *last == b' ' || *last == b'\t' {
            bytes = rest;
        } else {
            break;
        }
    }
    bytes
}

fn parse_hex(bytes: &[u8]) -> Option<usize> {
    if bytes.is_empty() || bytes.len() > 16 {
        return None;
    }
    let mut value: usize = 0;
    for &byte in bytes {
        let digit = match byte {
            b'0'..=b'9' => (byte - b'0') as usize,
            b'a'..=b'f' => (byte - b'a') as usize + 10,
            b'A'..=b'F' => (byte - b'A') as usize + 10,
            _ => return None,
        };
        value = value.checked_mul(16)?.checked_add(digit)?;
    }
    Some(value)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn decodes_two_chunks_and_terminator() {
        let decoded = dechunk(b"4\r\nWiki\r\n5\r\npedia\r\n0\r\n\r\n", 0).unwrap();
        assert_eq!(decoded.body, b"Wikipedia");
        assert_eq!(decoded.consumed, 24);
        assert!(decoded.trailers.is_empty());
    }

    #[test]
    fn accepts_chunk_extensions() {
        let decoded = dechunk(b"4;foo=bar\r\nWiki\r\n0\r\n\r\n", 0).unwrap();
        assert_eq!(decoded.body, b"Wiki");
    }

    #[test]
    fn parses_trailers() {
        let decoded = dechunk(b"0\r\nX-Sum: 1\r\nX-Other:\tv\r\n\r\n", 0).unwrap();
        assert_eq!(decoded.body, b"");
        assert_eq!(decoded.trailers.len(), 2);
        assert_eq!(decoded.trailers[0].0, "X-Sum");
        assert_eq!(decoded.trailers[0].1, b"1");
        assert_eq!(decoded.trailers[1].1, b"v");
    }

    #[test]
    fn truncated_chunk_is_an_error_not_a_partial_body() {
        // Data ends before the declared size.
        assert!(matches!(
            dechunk(b"4\r\nWik", 0),
            Err(DecodeError::TruncatedChunk { .. })
        ));
        // Data is complete but the trailing CRLF has not arrived: this is an
        // incomplete stream, not a malformed one (see message_complete).
        assert_eq!(
            dechunk(b"4\r\nWiki", 0),
            Err(DecodeError::MissingTerminator)
        );
    }

    #[test]
    fn bad_hex_is_an_error() {
        assert!(matches!(
            dechunk(b"zz\r\n", 0),
            Err(DecodeError::BadChunkSize { .. })
        ));
    }

    #[test]
    fn malformed_trailer_is_reported_as_a_trailer_error() {
        assert!(matches!(
            dechunk(b"0\r\nno-colon\r\n\r\n", 0),
            Err(DecodeError::MalformedTrailer { .. })
        ));
        assert!(matches!(
            dechunk(b"0\r\n: empty-name\r\n\r\n", 0),
            Err(DecodeError::MalformedTrailer { .. })
        ));
    }

    #[test]
    fn missing_terminator_is_an_error() {
        assert_eq!(
            dechunk(b"4\r\nWiki\r\n", 0),
            Err(DecodeError::MissingTerminator)
        );
    }

    #[test]
    fn chunk_data_without_crlf_is_an_error() {
        assert!(matches!(
            dechunk(b"4\r\nWikiXX0\r\n\r\n", 0),
            Err(DecodeError::MissingChunkCrlf { .. })
        ));
    }

    #[test]
    fn byte_values_survive_round_trip() {
        let payload: Vec<u8> = (0u8..=255).collect();
        let mut framed = Vec::new();
        framed.extend_from_slice(format!("{:x}\r\n", payload.len()).as_bytes());
        framed.extend_from_slice(&payload);
        framed.extend_from_slice(b"\r\n0\r\n\r\n");
        let decoded = dechunk(&framed, 0).unwrap();
        assert_eq!(decoded.body, payload);
    }
}
