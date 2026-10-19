// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

use async_trait::async_trait;
use http_body_util::{BodyExt, Full};
use hyper::body::Bytes;
use hyper_util::client::legacy::Client;
use hyper_util::rt::TokioExecutor;
use serde::Deserialize;
use std::fs;

use crate::responsibility::observer::llm::ObserverLLM;
use crate::responsibility::observer::system_prompt::OBSERVER_SYSTEM_PROMPT;
use crate::responsibility::observer::types::{ObserverError, ObserverInput, ObserverOutput};

#[derive(Debug)]
pub enum ObserverProviderError {
    ApiError(String),
    ValidationError(String),
    InjectionDetected,
    Timeout,
    ConfigError(String),
}

#[async_trait]
pub trait ObserverProvider: Send + Sync {
    async fn evaluate(
        &self,
        input: &ObserverInput,
    ) -> Result<ObserverOutput, ObserverProviderError>;
}

#[derive(Debug, Clone, Deserialize)]
pub struct ObserverProviderConfig {
    pub base_url: String,
    pub auth_token: String,
    pub model: String,
    #[serde(default = "default_timeout")]
    pub timeout_ms: u64,
    #[serde(default = "default_retries")]
    pub max_retries: u32,
}

fn default_timeout() -> u64 {
    30000
}
fn default_retries() -> u32 {
    3
}

pub struct ObserverLLMProvider {
    config: ObserverProviderConfig,
}

impl ObserverLLMProvider {
    pub fn from_config_file(path: &str) -> Result<Self, ObserverProviderError> {
        let content = fs::read_to_string(path)
            .map_err(|e| ObserverProviderError::ConfigError(e.to_string()))?;
        let config: ObserverProviderConfig = toml::from_str(&content)
            .map_err(|e| ObserverProviderError::ConfigError(e.to_string()))?;
        Self::new(config)
    }

    /// Load the first usable config file from `paths`.
    ///
    /// `Ok(None)` means no file exists, so the caller may legitimately fall
    /// back to the Mock provider. A file that exists but cannot be read or
    /// parsed is an error: reporting it as "not found" silently disables the
    /// LLM arbitration the operator configured.
    pub fn load_from_paths(paths: &[&str]) -> Result<Option<Self>, ObserverProviderError> {
        let mut errors: Vec<String> = Vec::new();
        for path in paths {
            if !std::path::Path::new(path).exists() {
                continue;
            }
            match Self::from_config_file(path) {
                Ok(provider) => return Ok(Some(provider)),
                Err(err) => errors.push(format!("{path}: {err:?}")),
            }
        }
        if errors.is_empty() {
            Ok(None)
        } else {
            Err(ObserverProviderError::ConfigError(errors.join("; ")))
        }
    }

    pub fn from_env() -> Result<Self, ObserverProviderError> {
        let config = ObserverProviderConfig {
            base_url: std::env::var("ANTHROPIC_BASE_URL")
                .ok()
                .filter(|v| !v.is_empty())
                .unwrap_or_else(|| "https://api.deepseek.com/anthropic".into()),
            auth_token: std::env::var("ANTHROPIC_AUTH_TOKEN").map_err(|_| {
                ObserverProviderError::ConfigError("ANTHROPIC_AUTH_TOKEN not set".into())
            })?,
            // deepseek-v4-flash: 最低成本, 足够分类仲裁.
            // Filter empty strings: sudo --preserve-env may pass empty MODEL.
            model: std::env::var("MODEL")
                .ok()
                .filter(|v| !v.is_empty())
                .unwrap_or_else(|| "deepseek-v4-flash".into()),
            timeout_ms: 15000, // 15s 快速失败
            max_retries: 1,    // 单次重试
        };
        Ok(Self { config })
    }

    pub fn new(config: ObserverProviderConfig) -> Result<Self, ObserverProviderError> {
        Ok(Self { config })
    }
}

#[async_trait]
impl ObserverProvider for ObserverLLMProvider {
    async fn evaluate(
        &self,
        input: &ObserverInput,
    ) -> Result<ObserverOutput, ObserverProviderError> {
        let prompt = ObserverLLM::build_compact_prompt(
            &input.signature,
            &input.prior,
            input.content_signals.as_ref(),
        );

        // Build request body for Anthropic-compatible API (DeepSeek).
        // Token budget:
        //   - max_tokens=2048: ample room for reasoning + JSON (was 500, which
        //     caused truncation when thinking blocks consumed most tokens).
        //   - thinking disabled: eliminates wasted tokens on internal reasoning
        //     that DeepSeek v4-flash would otherwise consume.
        //   - System prompt: guides JSON output format for higher consistency.
        //   - Compact user prompt: ~400-600 tokens.
        let body = serde_json::json!({
            "model": self.config.model,
            "max_tokens": 2048,
            "thinking": {"type": "disabled"},
            "messages": [
                {"role": "system", "content": OBSERVER_SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ]
        });

        let url = format!("{}/v1/messages", self.config.base_url.trim_end_matches('/'));

        let body_bytes = Bytes::from(serde_json::to_vec(&body).unwrap_or_default());

        // Use HTTPS connector via hyper-rustls for TLS support.
        // build_http() only handles plain HTTP — for api.deepseek.com HTTPS
        // we need the rustls-backed HttpsConnector.
        let https = hyper_rustls::HttpsConnectorBuilder::new()
            .with_webpki_roots()
            .https_or_http()
            .enable_http1()
            .build();
        let client: Client<_, Full<Bytes>> = Client::builder(TokioExecutor::new()).build(https);

        let req = hyper::Request::builder()
            .method("POST")
            .uri(&url)
            .header("x-api-key", &self.config.auth_token)
            .header("anthropic-version", "2023-06-01")
            .header("content-type", "application/json")
            .body(Full::new(body_bytes))
            .map_err(|e| ObserverProviderError::ApiError(e.to_string()))?;

        let request_future = client.request(req);

        let resp = tokio::time::timeout(
            std::time::Duration::from_millis(self.config.timeout_ms),
            request_future,
        )
        .await
        .map_err(|_| ObserverProviderError::Timeout)?
        .map_err(|e| ObserverProviderError::ApiError(e.to_string()))?;

        let status = resp.status();
        let body_bytes = resp
            .into_body()
            .collect()
            .await
            .map_err(|e| ObserverProviderError::ApiError(e.to_string()))?
            .to_bytes();

        if !status.is_success() {
            let text = String::from_utf8_lossy(&body_bytes);
            return Err(ObserverProviderError::ApiError(format!(
                "HTTP {}: {}",
                status.as_u16(),
                text
            )));
        }

        let raw: serde_json::Value = serde_json::from_slice(&body_bytes)
            .map_err(|e| ObserverProviderError::ApiError(e.to_string()))?;

        // DeepSeek Anthropic-compatible API returns thinking blocks first.
        // Strategy: prefer block with type="text", then any block with "text" field,
        // then concatenate all thinking blocks, then fallback to first block.
        let content_blocks = raw["content"].as_array();
        let text = content_blocks
            .and_then(|blocks| {
                // 1. Try to find a block with type="text" that has a "text" field
                blocks
                    .iter()
                    .find(|b| {
                        b.get("type").and_then(|t| t.as_str()) == Some("text")
                            && b.get("text").is_some()
                    })
                    // 2. Try any block with a "text" field
                    .or_else(|| blocks.iter().find(|b| b.get("text").is_some()))
                    // 3. Fall back to first block
                    .or_else(|| blocks.first())
            })
            .and_then(|block| block["text"].as_str())
            // A response with no text block (thinking-only, or an empty body)
            // used to fall back to the literal "{}". That parses against
            // `RawObserverResponse`'s serde defaults, yielding a *valid*
            // verdict of decision="uncertain", severity="warning",
            // confidence=0.5 (see observer/llm.rs) — so a model that never
            // answered was reported as having given a reasoned 50% judgement.
            // Surface it as an error instead.
            .ok_or_else(|| {
                ObserverProviderError::ValidationError(
                    "observer response contained no text block; \
                     the model returned nothing usable"
                        .into(),
                )
            })?;

        // Debug: uncomment to see raw API response
        // eprintln!("[observer] raw response: {}", String::from_utf8_lossy(&body_bytes[..body_bytes.len().min(500)]));

        ObserverLLM::parse_and_validate(text).map_err(|e| match e {
            ObserverError::InjectionDetected => ObserverProviderError::InjectionDetected,
            ObserverError::SelfReportedAttack => ObserverProviderError::InjectionDetected,
            _ => ObserverProviderError::ValidationError(format!("{:?}", e)),
        })
    }
}

#[cfg(test)]
mod config_loader_tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn missing_files_are_not_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let a = dir.path().join("a.toml");
        let b = dir.path().join("b.toml");
        let loaded =
            ObserverLLMProvider::load_from_paths(&[a.to_str().unwrap(), b.to_str().unwrap()])
                .expect("missing files mean 'not configured'");
        assert!(loaded.is_none());
    }

    #[test]
    fn existing_but_invalid_config_is_an_error() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("observer.toml");
        std::fs::File::create(&path)
            .unwrap()
            .write_all(b"base_url = [this is not toml")
            .unwrap();

        match ObserverLLMProvider::load_from_paths(&[path.to_str().unwrap()]) {
            Err(ObserverProviderError::ConfigError(_)) => {}
            Err(other) => panic!("wrong error kind: {other:?}"),
            Ok(_) => panic!("an unparseable config must not be reported as not-found"),
        }
    }

    #[test]
    fn first_valid_config_wins() {
        let dir = tempfile::tempdir().unwrap();
        let good = dir.path().join("good.toml");
        std::fs::File::create(&good)
            .unwrap()
            .write_all(b"base_url = \"http://127.0.0.1\"\nauth_token = \"t\"\nmodel = \"m\"\n")
            .unwrap();

        let loaded =
            ObserverLLMProvider::load_from_paths(&[good.to_str().unwrap()]).expect("valid config");
        assert!(loaded.is_some());
    }
}
