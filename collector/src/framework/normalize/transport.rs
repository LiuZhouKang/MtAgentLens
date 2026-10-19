// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

//! Transport-line payload decoding.
//!
//! New captures carry `data_b64` (authoritative bytes) next to the legacy
//! `data` display string. Old captures only carry `data`, whose bytes the C
//! producer escaped. That escape is invertible **on the raw line bytes**:
//! `\u00XX` came from exactly one captured byte.
//!
//! It is *not* invertible after `serde_json` has parsed the line: serde maps
//! `µ` to the UTF-8 encoding of U+00B5 (two bytes), which is also what a
//! raw `C2 B5` sequence becomes. That is why the legacy path takes `&[u8]`,
//! never a `serde_json::Value`.
//!
//! Contract: a decoded payload's length must equal the declared `buf_size`.
//! A mismatch is an error, not a warning: it means bytes were lost.

use std::fmt;

use base64::Engine as _;
use base64::engine::general_purpose::STANDARD as BASE64;
use serde_json::Value;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TransportError {
    /// `\uXXXX` with a value that cannot have come from one captured byte.
    UnsupportedEscape(u32),
    /// A backslash escape the producer never emits.
    UnknownEscape(u8),
    /// The `data` string ended without a closing quote.
    UnterminatedData,
    /// Decoded bytes disagree with the declared `buf_size`.
    LengthMismatch { declared: u32, decoded: usize },
    /// `data_b64` is present but not valid standard base64.
    InvalidBase64(String),
    /// A numeric field is not a number.
    MalformedField(&'static str),
}

impl fmt::Display for TransportError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::UnsupportedEscape(value) => {
                write!(
                    f,
                    "escape \\u{value:04x} cannot represent one captured byte"
                )
            }
            Self::UnknownEscape(byte) => write!(f, "unknown escape \\{}", *byte as char),
            Self::UnterminatedData => write!(f, "unterminated data string"),
            Self::LengthMismatch { declared, decoded } => {
                write!(f, "declared buf_size {declared} != decoded {decoded}")
            }
            Self::InvalidBase64(message) => write!(f, "invalid data_b64: {message}"),
            Self::MalformedField(name) => write!(f, "malformed numeric field {name}"),
        }
    }
}

impl std::error::Error for TransportError {}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PayloadSource {
    /// The producer emitted `data_b64` directly.
    Base64,
    /// An old capture; bytes were recovered from the raw line's escapes.
    LegacyUnescape,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WirePayload {
    pub bytes: Vec<u8>,
    pub source: PayloadSource,
    pub declared_buf_size: u32,
    pub truncated: bool,
}

const DATA_KEY: &[u8] = b"\"data\":\"";
const DATA_B64_SOURCE_KEY: &str = "data_b64_source";

/// Recover the exact bytes behind a legacy `data` string.
///
/// Returns `Ok(None)` when the line has no payload (`"data":null` or no such
/// field at all). The scan is over raw bytes and mirrors
/// `bpftools/tls_capture/tls_capture.c`'s escaping rules.
pub fn legacy_data_bytes(line: &[u8]) -> Result<Option<Vec<u8>>, TransportError> {
    let start = match find(line, DATA_KEY) {
        Some(position) => position + DATA_KEY.len(),
        None => return Ok(None),
    };

    let mut out = Vec::new();
    let mut index = start;
    while index < line.len() {
        let byte = line[index];
        match byte {
            b'"' => return Ok(Some(out)),
            b'\\' => {
                let escaped = *line
                    .get(index + 1)
                    .ok_or(TransportError::UnterminatedData)?;
                match escaped {
                    b'"' => out.push(b'"'),
                    b'\\' => out.push(b'\\'),
                    b'n' => out.push(b'\n'),
                    b'r' => out.push(b'\r'),
                    b't' => out.push(b'\t'),
                    b'b' => out.push(0x08),
                    b'f' => out.push(0x0c),
                    b'u' => {
                        let hex = line
                            .get(index + 2..index + 6)
                            .ok_or(TransportError::UnterminatedData)?;
                        let text = std::str::from_utf8(hex)
                            .map_err(|_| TransportError::UnknownEscape(b'u'))?;
                        let value = u32::from_str_radix(text, 16)
                            .map_err(|_| TransportError::UnknownEscape(b'u'))?;
                        if value > 0xFF {
                            return Err(TransportError::UnsupportedEscape(value));
                        }
                        out.push(value as u8);
                        index += 6;
                        continue;
                    }
                    other => return Err(TransportError::UnknownEscape(other)),
                }
                index += 2;
            }
            other => {
                out.push(other);
                index += 1;
            }
        }
    }
    Err(TransportError::UnterminatedData)
}

/// Ensure an SSL event carries `data_b64` and that it matches `buf_size`.
///
/// Idempotent. For a line with `data_b64` the existing value is validated; for
/// an old line the bytes are recovered from the raw line and re-encoded. Both
/// paths end with a length check, so corruption cannot pass silently.
pub fn ensure_data_b64(value: &mut Value, raw_line: &[u8]) -> Result<(), TransportError> {
    let object = match value.as_object_mut() {
        Some(object) => object,
        None => return Ok(()),
    };
    let declared = match object.get("buf_size").and_then(Value::as_u64) {
        Some(declared) => declared as u32,
        None => return Ok(()), // not an SSL payload event
    };

    if let Some(existing) = object.get("data_b64") {
        match existing {
            Value::Null => return Ok(()),
            Value::String(encoded) => {
                let bytes = BASE64
                    .decode(encoded)
                    .map_err(|error| TransportError::InvalidBase64(error.to_string()))?;
                if bytes.len() != declared as usize {
                    return Err(TransportError::LengthMismatch {
                        declared,
                        decoded: bytes.len(),
                    });
                }
                return Ok(());
            }
            _ => {
                return Err(TransportError::MalformedField("data_b64"));
            }
        }
    }

    let recovered = legacy_data_bytes(raw_line)?;
    let (encoded, source) = match recovered {
        Some(bytes) => {
            if bytes.len() != declared as usize {
                return Err(TransportError::LengthMismatch {
                    declared,
                    decoded: bytes.len(),
                });
            }
            (Value::String(BASE64.encode(&bytes)), "legacy_unescape")
        }
        None => (Value::Null, "legacy_null"),
    };
    let object = value.as_object_mut().expect("checked above");
    object.insert("data_b64".to_string(), encoded);
    object.insert(
        DATA_B64_SOURCE_KEY.to_string(),
        Value::String(source.to_string()),
    );
    Ok(())
}

/// Read the authoritative bytes from a normalized event.
///
/// Returns `Ok(None)` when the event declares no payload. A missing field is
/// also `None`: synthetic events are not expected to carry transport bytes.
pub fn payload_bytes(value: &Value) -> Result<Option<WirePayload>, TransportError> {
    let object = match value.as_object() {
        Some(object) => object,
        None => return Ok(None),
    };
    let declared = match object.get("buf_size").and_then(Value::as_u64) {
        Some(declared) => declared as u32,
        None => return Ok(None),
    };
    let bytes = match object.get("data_b64") {
        Some(Value::String(encoded)) => BASE64
            .decode(encoded)
            .map_err(|error| TransportError::InvalidBase64(error.to_string()))?,
        Some(Value::Null) | None => return Ok(None),
        Some(_) => return Err(TransportError::MalformedField("data_b64")),
    };
    if bytes.len() != declared as usize {
        return Err(TransportError::LengthMismatch {
            declared,
            decoded: bytes.len(),
        });
    }
    let source = match object.get(DATA_B64_SOURCE_KEY).and_then(Value::as_str) {
        Some("legacy_unescape") | Some("legacy_null") => PayloadSource::LegacyUnescape,
        _ => PayloadSource::Base64,
    };
    Ok(Some(WirePayload {
        bytes,
        source,
        declared_buf_size: declared,
        truncated: object
            .get("truncated")
            .and_then(Value::as_bool)
            .unwrap_or(false),
    }))
}

fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    if needle.is_empty() || haystack.len() < needle.len() {
        return None;
    }
    haystack
        .windows(needle.len())
        .position(|window| window == needle)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn legacy_line(data: &str, buf_size: u32) -> Vec<u8> {
        format!(
            "{{\"function\":\"READ/RECV\",\"len\":{buf_size},\"buf_size\":{buf_size},\"data\":\"{data}\"}}"
        )
        .into_bytes()
    }

    #[test]
    fn recovers_escaped_high_bytes_as_single_bytes() {
        let line = legacy_line("\\u00b5\\u00e9", 2);
        assert_eq!(legacy_data_bytes(&line).unwrap(), Some(vec![0xB5, 0xE9]));
    }

    #[test]
    fn keeps_valid_utf8_sequences_verbatim() {
        let mut line = b"{\"buf_size\":2,\"data\":\"".to_vec();
        line.extend_from_slice(&[0xC2, 0xB5]);
        line.extend_from_slice(b"\"}");
        assert_eq!(legacy_data_bytes(&line).unwrap(), Some(vec![0xC2, 0xB5]));
    }

    #[test]
    fn null_data_is_no_payload() {
        let line = b"{\"buf_size\":0,\"data\":null}";
        assert_eq!(legacy_data_bytes(line).unwrap(), None);
    }

    #[test]
    fn unterminated_data_is_an_error() {
        let line = b"{\"buf_size\":4,\"data\":\"abc}";
        assert_eq!(
            legacy_data_bytes(line),
            Err(TransportError::UnterminatedData)
        );
    }

    #[test]
    fn ensure_is_idempotent_and_validates_base64() {
        let mut value = json!({"buf_size": 3, "data_b64": "YWJj", "data": "abc"});
        ensure_data_b64(&mut value, b"{}").unwrap();
        assert_eq!(value["data_b64"], "YWJj");
        let payload = payload_bytes(&value).unwrap().unwrap();
        assert_eq!(payload.bytes, b"abc");
        assert_eq!(payload.source, PayloadSource::Base64);
    }

    #[test]
    fn ensure_recovers_legacy_bytes_and_marks_them() {
        let line = legacy_line("\\u00b5", 1);
        let mut value: Value = serde_json::from_slice(&line).unwrap();
        ensure_data_b64(&mut value, &line).unwrap();
        assert_eq!(value["data_b64"], "tQ==");
        assert_eq!(value["data_b64_source"], "legacy_unescape");
        let payload = payload_bytes(&value).unwrap().unwrap();
        assert_eq!(payload.bytes, vec![0xB5]);
        assert_eq!(payload.source, PayloadSource::LegacyUnescape);
    }

    #[test]
    fn length_mismatch_is_an_error() {
        let mut value = json!({"buf_size": 4});
        let line = legacy_line("abc", 4);
        // The line says 4 but only carries three bytes.
        assert!(matches!(
            ensure_data_b64(&mut value, &line),
            Err(TransportError::LengthMismatch { .. })
        ));
    }

    #[test]
    fn invalid_base64_is_an_error() {
        let mut value = json!({"buf_size": 3, "data_b64": "!!!"});
        assert!(matches!(
            ensure_data_b64(&mut value, b"{}"),
            Err(TransportError::InvalidBase64(_))
        ));
    }
}
