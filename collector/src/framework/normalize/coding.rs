// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Content-coding decoding (RFC 9110 §8.4) in reverse application order.
//!
//! Deliverable constraint from P0.5: the zstd window is capped at 8 MiB
//! (RFC 9659 §3), so a hostile frame cannot make the collector allocate
//! gigabytes. An unknown coding is an error — never a pass-through, because a
//! pass-through is indistinguishable from a successful decode downstream.

use std::io::Read;

use super::error::DecodeError;

/// 2^23 = 8 MiB, the RFC 9659 §3 limit for zstd in HTTP.
pub const MAX_ZSTD_WINDOW_LOG: u32 = 23;

/// Split a `Content-Encoding` field value into lowercased codings.
pub fn parse_codings(value: &str) -> Vec<String> {
    value
        .split(',')
        .map(|part| part.trim().to_ascii_lowercase())
        .filter(|part| !part.is_empty())
        .collect()
}

/// Decode `body` according to `content_encoding`.
///
/// Returns the decoded bytes and the codings that were applied, in header
/// order (the first element was applied by the peer first).
pub fn decode_content(
    body: &[u8],
    content_encoding: Option<&str>,
) -> Result<(Vec<u8>, Vec<String>), DecodeError> {
    let codings = content_encoding.map(parse_codings).unwrap_or_default();
    let mut data = body.to_vec();
    // The codings are listed in the order they were applied, so the decoder
    // peels them off from the last one backwards.
    for coding in codings.iter().rev() {
        data = decode_one(&data, coding)?;
    }
    Ok((data, codings))
}

fn decode_one(data: &[u8], coding: &str) -> Result<Vec<u8>, DecodeError> {
    match coding {
        "" | "identity" => Ok(data.to_vec()),
        "zstd" => decode_zstd(data),
        "gzip" | "x-gzip" => {
            let mut decoder = flate2::read::GzDecoder::new(data);
            read_all(&mut decoder, "gzip")
        }
        // HTTP "deflate" is specified as zlib-wrapped (RFC 9110 §8.4.1.2), but
        // a meaningful fraction of servers send a raw deflate stream. Try the
        // specified form first and fall back only when it fails; if both fail,
        // report the zlib error (the specified one).
        "deflate" => {
            let mut zlib = flate2::read::ZlibDecoder::new(data);
            match read_all(&mut zlib, "deflate") {
                Ok(decoded) => Ok(decoded),
                Err(zlib_error) => {
                    let mut raw = flate2::read::DeflateDecoder::new(data);
                    read_all(&mut raw, "deflate").map_err(|_| zlib_error)
                }
            }
        }
        "br" => {
            let mut decoder = brotli::Decompressor::new(data, 4096);
            read_all(&mut decoder, "br")
        }
        other => Err(DecodeError::UnsupportedContentEncoding(other.to_string())),
    }
}

fn decode_zstd(data: &[u8]) -> Result<Vec<u8>, DecodeError> {
    let mut decoder =
        zstd::stream::read::Decoder::new(std::io::Cursor::new(data)).map_err(|error| {
            DecodeError::Decompress {
                encoding: "zstd".to_string(),
                message: error.to_string(),
            }
        })?;
    decoder
        .window_log_max(MAX_ZSTD_WINDOW_LOG)
        .map_err(|error| DecodeError::Decompress {
            encoding: "zstd".to_string(),
            message: format!("cannot enforce the window limit: {error}"),
        })
        .map(|_| ())
        .and_then(|_| {
            let mut out = Vec::new();
            decoder
                .read_to_end(&mut out)
                .map(|_| out)
                .map_err(|error| DecodeError::Decompress {
                    encoding: "zstd".to_string(),
                    message: error.to_string(),
                })
        })
}

fn read_all<R: Read>(reader: &mut R, encoding: &str) -> Result<Vec<u8>, DecodeError> {
    let mut out = Vec::new();
    reader
        .read_to_end(&mut out)
        .map_err(|error| DecodeError::Decompress {
            encoding: encoding.to_string(),
            message: error.to_string(),
        })?;
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identity_passthrough() {
        let (out, codings) = decode_content(b"abc", None).unwrap();
        assert_eq!(out, b"abc");
        assert!(codings.is_empty());

        let (out, codings) = decode_content(b"abc", Some("identity")).unwrap();
        assert_eq!(out, b"abc");
        assert_eq!(codings, vec!["identity"]);
    }

    #[test]
    fn unknown_coding_is_an_error() {
        assert_eq!(
            decode_content(b"abc", Some("snappy")),
            Err(DecodeError::UnsupportedContentEncoding("snappy".into()))
        );
    }

    #[test]
    fn gzip_round_trip() {
        use flate2::Compression;
        use flate2::write::GzEncoder;
        use std::io::Write;

        let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(b"hello gzip").unwrap();
        let compressed = encoder.finish().unwrap();
        let (out, codings) = decode_content(&compressed, Some("gzip")).unwrap();
        assert_eq!(out, b"hello gzip");
        assert_eq!(codings, vec!["gzip"]);
    }

    #[test]
    fn zstd_round_trip_and_reverse_order() {
        let compressed = zstd::stream::encode_all(&b"hello zstd"[..], 3).unwrap();
        let (out, codings) = decode_content(&compressed, Some("zstd")).unwrap();
        assert_eq!(out, b"hello zstd");
        assert_eq!(codings, vec!["zstd"]);
    }

    #[test]
    fn codings_are_peeled_in_reverse_order() {
        // Peer applied zstd first, then gzip: header says "zstd, gzip".
        let zstd = zstd::stream::encode_all(&b"layered"[..], 3).unwrap();
        use flate2::Compression;
        use flate2::write::GzEncoder;
        use std::io::Write;
        let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(&zstd).unwrap();
        let outer = encoder.finish().unwrap();
        let (out, codings) = decode_content(&outer, Some("zstd, gzip")).unwrap();
        assert_eq!(out, b"layered");
        assert_eq!(codings, vec!["zstd", "gzip"]);
    }

    #[test]
    fn corrupt_zstd_is_an_error() {
        assert!(matches!(
            decode_content(b"not a zstd frame", Some("zstd")),
            Err(DecodeError::Decompress { .. })
        ));
    }
}
