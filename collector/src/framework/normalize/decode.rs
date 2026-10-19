// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Compose head parsing, de-chunking and content decoding into one byte path.

use super::chunked::dechunk;
use super::coding::decode_content;
use super::error::DecodeError;
use super::http_head::{HttpHead, split_head};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DecodedMessage {
    pub head: HttpHead,
    /// Fully de-framed and decompressed body.
    pub body: Vec<u8>,
    /// Content codings that were applied, in header order.
    pub codings: Vec<String>,
    /// Bytes consumed from the input. Trailing bytes (a pipelined next
    /// message) are reported here rather than silently ignored.
    pub consumed: usize,
}

/// Decode one complete HTTP/1.x message from `raw`.
///
/// `raw` may contain more than one message; only the first is decoded and
/// `consumed` tells the caller where the next one starts.
pub fn decode_message(raw: &[u8]) -> Result<DecodedMessage, DecodeError> {
    let head = split_head(raw)?;
    let body_start = head.body_offset;

    let transfer_values = head.header_all("transfer-encoding");
    let (framed, consumed) = if !transfer_values.is_empty() {
        let combined = transfer_values.join(",");
        let codings: Vec<&str> = combined
            .split(',')
            .map(str::trim)
            .filter(|part| !part.is_empty())
            .collect();
        let last = codings.last().copied().unwrap_or("");
        if !last.eq_ignore_ascii_case("chunked") {
            return Err(DecodeError::UnsupportedTransferEncoding(combined));
        }
        // Transfer codings other than chunked (e.g. "gzip, chunked") are legal
        // on the wire but not implemented; refusing is the honest answer.
        if codings
            .iter()
            .any(|coding| !coding.eq_ignore_ascii_case("chunked"))
        {
            return Err(DecodeError::UnsupportedTransferEncoding(combined));
        }
        let decoded = dechunk(&raw[body_start..], body_start)?;
        (decoded.body, body_start + decoded.consumed)
    } else if let Some(value) = head.header("content-length") {
        let declared: usize = value
            .trim()
            .parse()
            .map_err(|_| DecodeError::MalformedHeader {
                line: format!("Content-Length: {value}"),
            })?;
        let available = raw.len().saturating_sub(body_start);
        if available < declared {
            return Err(DecodeError::IncompleteBody {
                declared,
                actual: available,
            });
        }
        (
            raw[body_start..body_start + declared].to_vec(),
            body_start + declared,
        )
    } else {
        (raw[body_start..].to_vec(), raw.len())
    };

    let content_encoding = {
        let values = head.header_all("content-encoding");
        if values.is_empty() {
            None
        } else {
            Some(values.join(","))
        }
    };
    let (body, codings) = decode_content(&framed, content_encoding.as_deref())?;

    Ok(DecodedMessage {
        head,
        body,
        codings,
        consumed,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chunked_response(headers: &str, body: &[u8]) -> Vec<u8> {
        let mut message = format!("HTTP/1.1 200 OK\r\n{headers}\r\n").into_bytes();
        message.extend_from_slice(body);
        message
    }

    #[test]
    fn decodes_chunked_message_and_reports_consumed() {
        let raw = chunked_response(
            "Transfer-Encoding: chunked\r\n",
            b"4\r\nWiki\r\n5\r\npedia\r\n0\r\n\r\nNEXT",
        );
        let decoded = decode_message(&raw).unwrap();
        assert_eq!(decoded.body, b"Wikipedia");
        assert_eq!(&raw[decoded.consumed..], b"NEXT");
    }

    #[test]
    fn content_length_limits_body_and_leaves_the_rest() {
        let raw = b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\nabcXYZ";
        let decoded = decode_message(raw).unwrap();
        assert_eq!(decoded.body, b"abc");
        assert_eq!(decoded.consumed, raw.len() - 3);
    }

    #[test]
    fn transfer_encoding_wins_over_content_length() {
        let raw = chunked_response(
            "Content-Length: 999\r\nTransfer-Encoding: chunked\r\n",
            b"3\r\nabc\r\n0\r\n\r\n",
        );
        let decoded = decode_message(&raw).unwrap();
        assert_eq!(decoded.body, b"abc");
    }

    #[test]
    fn non_chunked_transfer_encoding_is_rejected() {
        let raw = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip\r\n\r\nxx";
        assert!(matches!(
            decode_message(raw),
            Err(DecodeError::UnsupportedTransferEncoding(_))
        ));
    }

    #[test]
    fn short_content_length_body_is_an_error() {
        let raw = b"HTTP/1.1 200 OK\r\nContent-Length: 10\r\n\r\nabc";
        assert!(matches!(
            decode_message(raw),
            Err(DecodeError::IncompleteBody { .. })
        ));
    }

    #[test]
    fn chunked_gzip_response_is_dechunked_then_decompressed() {
        use flate2::Compression;
        use flate2::write::GzEncoder;
        use std::io::Write;

        let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
        encoder.write_all(b"hello layered").unwrap();
        let compressed = encoder.finish().unwrap();
        let mut body = format!("{:x}\r\n", compressed.len()).into_bytes();
        body.extend_from_slice(&compressed);
        body.extend_from_slice(b"\r\n0\r\n\r\n");
        let raw = chunked_response(
            "Transfer-Encoding: chunked\r\nContent-Encoding: gzip\r\n",
            &body,
        );
        let decoded = decode_message(&raw).unwrap();
        assert_eq!(decoded.body, b"hello layered");
        assert_eq!(decoded.codings, vec!["gzip"]);
    }
}
