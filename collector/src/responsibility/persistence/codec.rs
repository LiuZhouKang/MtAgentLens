// SPDX-License-Identifier: Apache-2.0 OR MIT
//! Compressed JSON payloads for large analysis checkpoints.
//!
//! Frozen checkpoints embed the graph, evidence and actions and reach tens of
//! MB. Storing them as plain text duplicates facts already held in normalized
//! tables and is the largest remaining DB cost. Small payloads stay readable
//! text for compatibility; large ones are zstd-compressed and base64-encoded
//! with an explicit prefix, so old/plain rows keep working.

use base64::Engine as _;

const PREFIX: &str = "zstd1:";
const COMPRESS_THRESHOLD: usize = 4096;

pub fn encode_json(payload: &str) -> Result<String, String> {
    if payload.len() <= COMPRESS_THRESHOLD {
        return Ok(payload.to_owned());
    }
    let compressed =
        zstd::encode_all(payload.as_bytes(), 3).map_err(|e| format!("zstd encode failed: {e}"))?;
    let encoded = base64::engine::general_purpose::STANDARD.encode(compressed);
    Ok(format!("{PREFIX}{encoded}"))
}

pub fn decode_json(payload: &str) -> Result<String, String> {
    let Some(encoded) = payload.strip_prefix(PREFIX) else {
        return Ok(payload.to_owned());
    };
    let compressed = base64::engine::general_purpose::STANDARD
        .decode(encoded)
        .map_err(|e| format!("base64 decode failed: {e}"))?;
    let bytes =
        zstd::decode_all(compressed.as_slice()).map_err(|e| format!("zstd decode failed: {e}"))?;
    String::from_utf8(bytes).map_err(|e| format!("decoded checkpoint is not UTF-8: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn small_payloads_stay_plain_text() {
        assert_eq!(encode_json("{\"a\":1}").unwrap(), "{\"a\":1}");
        assert_eq!(decode_json("{\"a\":1}").unwrap(), "{\"a\":1}");
    }

    #[test]
    fn large_payloads_round_trip_compressed() {
        let payload = "{\"graph\":\"".to_owned() + &"x".repeat(20_000) + "\"}";
        let encoded = encode_json(&payload).unwrap();
        assert!(encoded.starts_with(PREFIX));
        assert!(encoded.len() < payload.len());
        assert_eq!(decode_json(&encoded).unwrap(), payload);
    }

    #[test]
    fn corrupt_compressed_payload_is_visible() {
        assert!(decode_json("zstd1:not-base64").is_err());
    }
}
