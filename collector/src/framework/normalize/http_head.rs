// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! HTTP/1.x head parsing over raw bytes.
//!
//! Header values are kept as bytes: RFC 9110 allows obs-text, and a decoder
//! that turns them into `String` with lossy replacement would silently change
//! what the peer sent. Lookups return `&str` only when the value is valid UTF-8.

use super::error::DecodeError;

pub(crate) const HEAD_TERMINATOR: &[u8] = b"\r\n\r\n";

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HttpHead {
    /// The request or status line, decoded as UTF-8 (ASCII per RFC 9112 §3).
    pub start_line: String,
    /// Header fields in wire order, name as sent, value as raw bytes.
    pub headers: Vec<(String, Vec<u8>)>,
    /// Offset of the first body byte in the original buffer.
    pub body_offset: usize,
}

impl HttpHead {
    /// First header matching `name` (ASCII case-insensitive), if it is UTF-8.
    pub fn header(&self, name: &str) -> Option<&str> {
        self.header_all(name).into_iter().next()
    }

    /// Every header matching `name`, in wire order, that is valid UTF-8.
    pub fn header_all(&self, name: &str) -> Vec<&str> {
        self.headers
            .iter()
            .filter(|(field, _)| field.eq_ignore_ascii_case(name))
            .filter_map(|(_, value)| std::str::from_utf8(value).ok())
            .collect()
    }

    pub fn is_response(&self) -> bool {
        self.start_line.starts_with("HTTP/")
    }

    fn validate_start_line(line: &str) -> Result<(), DecodeError> {
        if line.is_empty() {
            return Err(DecodeError::MalformedStartLine);
        }
        if let Some(rest) = line.strip_prefix("HTTP/") {
            // "HTTP/1.1 200 OK" — version, SP, 3-digit status, optional reason.
            let mut parts = rest.splitn(2, ' ');
            let version = parts.next().unwrap_or("");
            let status = parts.next().unwrap_or("");
            let valid_version = matches!(version, "1.0" | "1.1");
            let code = status.split(' ').next().unwrap_or("");
            let valid_status = code.len() == 3 && code.bytes().all(|b| b.is_ascii_digit());
            if !valid_version || !valid_status {
                return Err(DecodeError::MalformedStartLine);
            }
            return Ok(());
        }
        // "METHOD target HTTP/1.1". Extension methods (e.g. M-SEARCH) contain
        // token characters beyond A-Z, so validate the RFC 9110 token set.
        if line.ends_with(" HTTP/1.1") || line.ends_with(" HTTP/1.0") {
            let mut parts = line.split(' ');
            let method = parts.next().unwrap_or("");
            let target = parts.next().unwrap_or("");
            if !method.is_empty() && !target.is_empty() && method.bytes().all(is_token_byte) {
                return Ok(());
            }
        }
        Err(DecodeError::MalformedStartLine)
    }
}

fn is_token_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || b"!#$%&'*+-.^_`|~".contains(&byte)
}

/// Split a raw HTTP/1.x message into a parsed head and its body offset.
///
/// Only the start line is required to be UTF-8. Header *values* are kept as
/// bytes: RFC 9110 permits obs-text, and forcing them through `String` would
/// either reject real traffic or silently replace bytes.
pub fn split_head(raw: &[u8]) -> Result<HttpHead, DecodeError> {
    let head_end = find(raw, HEAD_TERMINATOR).ok_or(DecodeError::IncompleteHead)?;
    let head = &raw[..head_end];

    let mut lines: Vec<&[u8]> = Vec::new();
    let mut pos = 0;
    while pos <= head.len() {
        match find_crlf(head, pos) {
            Some(end) => {
                lines.push(&head[pos..end]);
                pos = end + 2;
            }
            None => {
                lines.push(&head[pos..]);
                break;
            }
        }
    }

    let start_line_bytes = lines.first().copied().unwrap_or(&[]);
    let start_line =
        std::str::from_utf8(start_line_bytes).map_err(|_| DecodeError::MalformedStartLine)?;
    HttpHead::validate_start_line(start_line)?;

    let mut headers = Vec::new();
    for line in &lines[1..] {
        if line.is_empty() {
            // An empty line inside the head can only appear as the terminator,
            // which is outside `head`. Treat it as malformed rather than skip.
            return Err(DecodeError::MalformedHeader {
                line: String::new(),
            });
        }
        let colon = line.iter().position(|&byte| byte == b':').ok_or_else(|| {
            DecodeError::MalformedHeader {
                line: String::from_utf8_lossy(line).into_owned(),
            }
        })?;
        let name_bytes = trim_ows(&line[..colon]);
        if name_bytes.is_empty() || !name_bytes.iter().all(|&byte| is_token_byte(byte)) {
            return Err(DecodeError::MalformedHeader {
                line: String::from_utf8_lossy(line).into_owned(),
            });
        }
        let name = String::from_utf8_lossy(name_bytes).into_owned();
        let value = trim_ows(&line[colon + 1..]).to_vec();
        headers.push((name, value));
    }

    Ok(HttpHead {
        start_line: start_line.to_string(),
        headers,
        body_offset: head_end + HEAD_TERMINATOR.len(),
    })
}

fn find_crlf(bytes: &[u8], from: usize) -> Option<usize> {
    if from > bytes.len() {
        return None;
    }
    bytes[from..]
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

fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_response_head_and_reports_body_offset() {
        let raw = b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\nX-A: b\r\n\r\nabc";
        let head = split_head(raw).unwrap();
        assert_eq!(head.start_line, "HTTP/1.1 200 OK");
        assert_eq!(head.header("content-length"), Some("3"));
        assert_eq!(head.header("X-A"), Some("b"));
        assert_eq!(head.body_offset, raw.len() - 3);
        assert!(head.is_response());
    }

    #[test]
    fn splits_request_head() {
        let raw = b"POST /v1/messages HTTP/1.1\r\nHost: x\r\n\r\n{}";
        let head = split_head(raw).unwrap();
        assert_eq!(head.start_line, "POST /v1/messages HTTP/1.1");
        assert_eq!(head.header("host"), Some("x"));
        assert!(!head.is_response());
    }

    #[test]
    fn extension_methods_are_accepted() {
        let raw = b"M-SEARCH /x HTTP/1.1\r\nHost: a\r\n\r\n";
        let head = split_head(raw).unwrap();
        assert_eq!(head.start_line, "M-SEARCH /x HTTP/1.1");
    }

    #[test]
    fn obs_text_header_values_are_preserved_as_bytes() {
        // RFC 9110 permits obs-text (0x80-0xFF) in field values. It must not
        // make the whole head unparseable, and it must not be replaced.
        let mut message = b"HTTP/1.1 200 OK\r\nX-Raw: ".to_vec();
        message.extend_from_slice(&[0xff, 0xfe]);
        message.extend_from_slice(b"\r\nContent-Length: 0\r\n\r\n");
        let head = split_head(&message).unwrap();
        assert_eq!(head.headers[0].1, vec![0xff, 0xfe]);
        // Not valid UTF-8: lookups return None rather than a lossy string.
        assert_eq!(head.header("x-raw"), None);
    }

    #[test]
    fn missing_terminator_is_incomplete() {
        assert_eq!(
            split_head(b"HTTP/1.1 200 OK\r\n"),
            Err(DecodeError::IncompleteHead)
        );
    }

    #[test]
    fn malformed_header_is_rejected_not_skipped() {
        let raw = b"HTTP/1.1 200 OK\r\nnot-a-header\r\n\r\n";
        assert!(matches!(
            split_head(raw),
            Err(DecodeError::MalformedHeader { .. })
        ));
    }

    #[test]
    fn header_names_are_case_insensitive() {
        let raw = b"HTTP/1.1 200 OK\r\nContent-Encoding: zstd\r\n\r\n";
        let head = split_head(raw).unwrap();
        assert_eq!(head.header("CONTENT-ENCODING"), Some("zstd"));
        assert_eq!(head.header_all("content-encoding"), vec!["zstd"]);
    }
}
