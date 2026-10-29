// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

use async_trait::async_trait;
use http_body_util::{BodyExt, Full, Limited};
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

/// Canonical DeepSeek API id for the economical V4.1 Flash tier.
/// The legacy `deepseek-v4-flash` alias is accepted but retired.
const DEFAULT_OBSERVER_MODEL: &str = "deepseek-flash";

fn resolve_observer_model(explicit: Option<&str>) -> String {
    explicit
        .filter(|model| !model.is_empty())
        .unwrap_or(DEFAULT_OBSERVER_MODEL)
        .to_string()
}

fn select_observer_model(
    explicit: Option<&str>,
    provider: Option<&str>,
    legacy: Option<&str>,
) -> String {
    resolve_observer_model(
        [explicit, provider, legacy]
            .into_iter()
            .flatten()
            .find(|value| !value.is_empty()),
    )
}

// Per-attempt ledger deliberately excludes request/response content and errors,
// which may contain credentials or provider response bodies.
static REQUEST_SEQUENCE: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);

fn next_request_id() -> u64 {
    REQUEST_SEQUENCE.fetch_add(1, std::sync::atomic::Ordering::Relaxed)
}

fn epoch_ns() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_nanos()
        .min(u64::MAX as u128) as u64
}

struct ObserverAttempt {
    model: String,
    request_id: u64,
    attempt: u32,
    start_ns: u64,
    end_ns: Option<u64>,
    started: std::time::Instant,
    elapsed_ms: Option<f64>,
    outcome: &'static str,
    usage: Option<serde_json::Value>,
}

impl ObserverAttempt {
    fn new(model: &str, request_id: u64, attempt: u32) -> Self {
        Self {
            model: model.into(),
            request_id,
            attempt,
            start_ns: epoch_ns(),
            end_ns: None,
            started: std::time::Instant::now(),
            elapsed_ms: None,
            outcome: "pending",
            usage: None,
        }
    }

    fn capture_usage(&mut self, raw: &serde_json::Value) {
        if let Some(usage) = raw.get("usage").filter(|value| value.is_object()) {
            let mut counts = serde_json::Map::new();
            for key in [
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
                "cache_hit_input_tokens",
                "cache_miss_input_tokens",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            ] {
                counts.insert(
                    key.into(),
                    usage
                        .get(key)
                        .filter(|v| v.is_u64())
                        .cloned()
                        .unwrap_or(serde_json::Value::Null),
                );
            }
            // Anthropic's cache creation durations are separate billing tiers.
            if let Some(cache) = usage.get("cache_creation").filter(|v| v.is_object()) {
                counts.insert("cache_creation".into(), serde_json::json!({
                    "ephemeral_5m_input_tokens": cache.get("ephemeral_5m_input_tokens").filter(|v| v.is_u64()),
                    "ephemeral_1h_input_tokens": cache.get("ephemeral_1h_input_tokens").filter(|v| v.is_u64()),
                }));
            }
            self.usage = Some(serde_json::Value::Object(counts));
        }
    }

    fn finish(&mut self, outcome: &'static str) {
        self.end_ns = Some(epoch_ns());
        self.elapsed_ms = Some(self.started.elapsed().as_secs_f64() * 1000.0);
        self.outcome = outcome;
    }

    fn json(&self) -> serde_json::Value {
        let pid = std::process::id();
        serde_json::json!({"schema_version": 1, "process_id": pid,
            "request_id": format!("{pid}-{}-{}", process_epoch_ns(), self.request_id),
            "attempt_id": format!("{pid}-{}-{}-{}", process_epoch_ns(), self.request_id, self.attempt),
            "attempt": self.attempt, "model": self.model,
            "start_epoch_ns": self.start_ns, "end_epoch_ns": self.end_ns,
            "elapsed_ms": self.elapsed_ms, "outcome": self.outcome,
            "success": self.outcome == "success", "usage": self.usage})
    }
}

fn process_epoch_ns() -> u64 {
    static START: std::sync::OnceLock<u64> = std::sync::OnceLock::new();
    *START.get_or_init(epoch_ns)
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
            // Economical DeepSeek V4.1 Flash tier for observer arbitration.
            // Filter empty strings: sudo --preserve-env may pass empty MODEL.
            model: select_observer_model(
                std::env::var("MTAGENTLENS_OBSERVER_MODEL").ok().as_deref(),
                std::env::var("ANTHROPIC_MODEL").ok().as_deref(),
                std::env::var("MODEL").ok().as_deref(),
            ),
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
        // `max_retries` is the number of attempts (the first call included).
        // Only transient failures are retried; a configuration or validation
        // error is deterministic and retrying it just delays the failure.
        let attempts = self.config.max_retries.max(1);
        let mut last_error = ObserverProviderError::Timeout;
        let request_id = next_request_id();
        for attempt in 0..attempts {
            let mut ledger = ObserverAttempt::new(&self.config.model, request_id, attempt + 1);
            let result = tokio::time::timeout(
                std::time::Duration::from_millis(self.config.timeout_ms),
                self.evaluate_with_deadline(input, &mut ledger),
            )
            .await;
            ledger.finish(match &result {
                Ok(Ok(_)) => "success",
                Ok(Err(ObserverProviderError::ValidationError(_))) => "validation_error",
                Ok(Err(ObserverProviderError::InjectionDetected)) => "validation_error",
                Ok(Err(ObserverProviderError::Timeout)) | Err(_) => "timeout",
                Ok(Err(_)) => "error",
            });
            eprintln!("[observer:attempt] {}", ledger.json());
            match result {
                Ok(Ok(output)) => return Ok(output),
                Ok(Err(error)) => {
                    if !is_retryable(&error) || attempt + 1 == attempts {
                        return Err(error);
                    }
                    last_error = error;
                }
                Err(_) => {
                    if attempt + 1 == attempts {
                        return Err(ObserverProviderError::Timeout);
                    }
                    last_error = ObserverProviderError::Timeout;
                }
            }
            let backoff_ms = (200u64 << attempt.min(3)).min(2_000);
            tokio::time::sleep(std::time::Duration::from_millis(backoff_ms)).await;
        }
        Err(last_error)
    }
}

/// Transient provider failures worth a second attempt.
fn is_retryable(error: &ObserverProviderError) -> bool {
    match error {
        ObserverProviderError::Timeout => true,
        ObserverProviderError::ApiError(message) => message
            .strip_prefix("HTTP ")
            .and_then(|rest| rest.get(..3))
            .and_then(|code| code.parse::<u16>().ok())
            .is_some_and(|code| code == 429 || (500..600).contains(&code)),
        _ => false,
    }
}

impl ObserverLLMProvider {
    async fn evaluate_with_deadline(
        &self,
        input: &ObserverInput,
        ledger: &mut ObserverAttempt,
    ) -> Result<ObserverOutput, ObserverProviderError> {
        const MAX_RESPONSE_BYTES: usize = 1024 * 1024;
        let prompt = ObserverLLM::build_compact_prompt(
            &input.signature,
            &input.prior,
            input.content_signals.as_ref(),
        );

        // Build request body for Anthropic-compatible API (DeepSeek).
        // Token budget:
        //   - max_tokens=4096: a per-candidate review returns one verdict per
        //     presented candidate (up to MAX_REVIEW_CANDIDATES=64), so the
        //     response is larger than the old batch verdict. 2048 could
        //     truncate the list, and a truncated list is rejected (exact ids),
        //     turning a reviewable run into observer_failed. This is a cap,
        //     not a cost: only generated tokens are billed.
        //   - thinking disabled: eliminates wasted tokens on internal reasoning
        //     that the Flash model would otherwise consume.
        //   - System prompt: guides JSON output format for higher consistency.
        //   - Compact user prompt: ~400-600 tokens.
        let body = serde_json::json!({
            "model": self.config.model,
            "max_tokens": 4096,
            // Deterministic arbitration. Without an explicit temperature the
            // provider default made the same evidence produce Safe and Unsafe
            // on consecutive calls (observed on benign-known-hosts-audit),
            // which makes any on/off paired experiment uninterpretable.
            "temperature": 0,
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

        let resp = client
            .request(req)
            .await
            .map_err(|e| ObserverProviderError::ApiError(e.to_string()))?;
        let status = resp.status();
        if resp
            .headers()
            .get("content-length")
            .and_then(|value| value.to_str().ok())
            .and_then(|value| value.parse::<u64>().ok())
            .is_some_and(|length| length > MAX_RESPONSE_BYTES as u64)
        {
            return Err(ObserverProviderError::ValidationError(
                "observer response exceeds 1 MiB".into(),
            ));
        }
        let body_bytes = Limited::new(resp.into_body(), MAX_RESPONSE_BYTES)
            .collect()
            .await
            .map_err(|e| ObserverProviderError::ApiError(e.to_string()))?
            .to_bytes();

        let parsed = serde_json::from_slice::<serde_json::Value>(&body_bytes);
        if let Ok(raw) = &parsed {
            ledger.capture_usage(raw);
        }
        if !status.is_success() {
            let text = String::from_utf8_lossy(&body_bytes);
            return Err(ObserverProviderError::ApiError(format!(
                "HTTP {}: {}",
                status.as_u16(),
                text
            )));
        }

        let raw = parsed.map_err(|e| ObserverProviderError::ApiError(e.to_string()))?;

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

        // Bind the per-candidate verdicts to the exact ids presented in this
        // request. Invented, duplicated and missing ids are rejected here so a
        // malformed list never reaches aggregation.
        let presented = input
            .content_signals
            .as_ref()
            .map(|signals| signals.candidates.as_slice())
            .unwrap_or(&[]);
        ObserverLLM::parse_and_validate_with_candidates(text, presented).map_err(|e| match e {
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
    fn attempt_ledger_preserves_unknown_usage_and_redacts_content() {
        let mut record = ObserverAttempt::new("test-model", next_request_id(), 1);
        record.capture_usage(&serde_json::json!({"usage": {"input_tokens": 10,
            "cache_read_input_tokens": 4, "secret": "do-not-log"},
            "content": "private prompt"}));
        record.finish("validation_error");
        let value = record.json();
        assert_eq!(value["usage"]["input_tokens"], 10);
        assert_eq!(value["usage"]["cache_read_input_tokens"], 4);
        assert!(value["usage"]["output_tokens"].is_null());
        assert_eq!(value["outcome"], "validation_error");
        assert!(!value.to_string().contains("do-not-log"));
        assert!(!value.to_string().contains("private prompt"));
        assert!(ObserverAttempt::new("m", next_request_id(), 1).json()["usage"].is_null());
    }

    #[test]
    fn attempt_ledger_retains_usage_when_output_validation_fails() {
        let raw = serde_json::json!({
            "usage": {"input_tokens": 42, "output_tokens": 8},
            "content": [{"type": "text", "text": "invalid-json"}]
        });
        let mut ledger = ObserverAttempt::new("m", next_request_id(), 1);
        ledger.capture_usage(&raw);
        let result = ObserverLLM::parse_and_validate_with_candidates(
            raw["content"][0]["text"].as_str().unwrap(),
            &[],
        );
        assert!(result.is_err());
        ledger.finish("validation_error");
        assert_eq!(ledger.json()["usage"]["output_tokens"], 8);
        assert_eq!(ledger.json()["success"], false);
    }

    #[test]
    fn attempt_ledger_ids_distinguish_retries_and_requests() {
        let request = next_request_id();
        let first = ObserverAttempt::new("m", request, 1).json();
        let retry = ObserverAttempt::new("m", request, 2).json();
        let other = ObserverAttempt::new("m", next_request_id(), 1).json();
        assert_eq!(first["request_id"], retry["request_id"]);
        assert_ne!(first["attempt_id"], retry["attempt_id"]);
        assert_ne!(first["attempt_id"], other["attempt_id"]);
    }

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
        let first = dir.path().join("first.toml");
        let second = dir.path().join("second.toml");
        for (path, model) in [(&first, "first-model"), (&second, "second-model")] {
            std::fs::File::create(path)
                .unwrap()
                .write_all(
                    format!(
                        "base_url = \"http://127.0.0.1\"\nauth_token = \"t\"\nmodel = \"{model}\"\n"
                    )
                    .as_bytes(),
                )
                .unwrap();
        }

        let loaded = ObserverLLMProvider::load_from_paths(&[
            first.to_str().unwrap(),
            second.to_str().unwrap(),
        ])
        .expect("valid config")
        .expect("first config is usable");
        assert_eq!(loaded.config.model, "first-model");
    }

    #[test]
    fn default_model_is_canonical_deepseek_flash() {
        assert_eq!(DEFAULT_OBSERVER_MODEL, "deepseek-flash");
        assert_eq!(resolve_observer_model(None), DEFAULT_OBSERVER_MODEL);
        assert_eq!(resolve_observer_model(Some("")), DEFAULT_OBSERVER_MODEL);
    }

    #[test]
    fn observer_model_selection_is_independent_of_cli_family_alias() {
        assert_eq!(
            select_observer_model(None, Some("deepseek-flash"), Some("haiku")),
            "deepseek-flash"
        );
        assert_eq!(
            select_observer_model(
                Some("explicit-observer"),
                Some("deepseek-flash"),
                Some("haiku")
            ),
            "explicit-observer"
        );
        assert_eq!(
            select_observer_model(Some(""), Some(""), Some("legacy-model")),
            "legacy-model"
        );
    }

    #[test]
    fn explicit_model_override_is_used_verbatim() {
        assert_eq!(
            resolve_observer_model(Some("deepseek-v4-pro")),
            "deepseek-v4-pro"
        );
    }

    #[test]
    fn config_file_model_is_used_verbatim() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("observer.toml");
        std::fs::File::create(&path)
            .unwrap()
            .write_all(
                b"base_url = \"http://127.0.0.1\"\nauth_token = \"t\"\nmodel = \"caller-model\"\n",
            )
            .unwrap();

        let loaded =
            ObserverLLMProvider::load_from_paths(&[path.to_str().unwrap()]).expect("valid config");
        assert_eq!(
            loaded.expect("config file is usable").config.model,
            "caller-model"
        );
    }

    #[test]
    fn retry_policy_covers_timeouts_and_server_errors_only() {
        assert!(is_retryable(&ObserverProviderError::Timeout));
        assert!(is_retryable(&ObserverProviderError::ApiError(
            "HTTP 429: slow down".into()
        )));
        assert!(is_retryable(&ObserverProviderError::ApiError(
            "HTTP 503: unavailable".into()
        )));
        // Deterministic failures must fail fast.
        assert!(!is_retryable(&ObserverProviderError::ApiError(
            "HTTP 400: bad request".into()
        )));
        assert!(!is_retryable(&ObserverProviderError::ValidationError(
            "observer response exceeds 1 MiB".into()
        )));
        assert!(!is_retryable(&ObserverProviderError::InjectionDetected));
        assert!(!is_retryable(&ObserverProviderError::ConfigError(
            "missing token".into()
        )));
    }
}
