// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 eunomia-bpf org.

use super::{EventStream, RunnerError};
use crate::framework::analyzers::Analyzer;
use futures::stream::Stream;
use log::debug;
use std::pin::Pin;
use std::process::Stdio;
use tokio::io::{AsyncBufRead, AsyncBufReadExt, BufReader};
use tokio::process::Command as TokioCommand;

/// Type alias for JSON stream
pub type JsonStream = Pin<Box<dyn Stream<Item = serde_json::Value> + Send>>;

/// One line of a child's JSON stdout.
pub(crate) enum JsonLine {
    Value(serde_json::Value),
    Skipped,
}

/// Render at most `limit` bytes for a log message without substituting
/// U+FFFD. Invalid UTF-8 is shown as hex; the data path never uses this.
fn display_bytes(bytes: &[u8], limit: usize) -> String {
    let cut = bytes.len().min(limit);
    match std::str::from_utf8(&bytes[..cut]) {
        Ok(text) => text.to_string(),
        Err(_) => {
            let hex = bytes
                .iter()
                .take(64)
                .map(|byte| format!("{byte:02x}"))
                .collect::<Vec<_>>()
                .join(" ");
            format!("<{} bytes non-utf8: {}>", bytes.len(), hex)
        }
    }
}

fn trim_ascii(bytes: &[u8]) -> &[u8] {
    let start = bytes
        .iter()
        .position(|byte| !matches!(byte, b' ' | b'\t' | b'\r' | b'\n'))
        .unwrap_or(bytes.len());
    let end = bytes
        .iter()
        .rposition(|byte| !matches!(byte, b' ' | b'\t' | b'\r' | b'\n'))
        .map_or(start, |index| index + 1);
    &bytes[start..end]
}

fn contains_ascii_case_insensitive(haystack: &[u8], needle: &[u8]) -> bool {
    haystack
        .windows(needle.len())
        .any(|window| window.eq_ignore_ascii_case(needle))
}

/// Read one newline-delimited JSON object from `reader`.
///
/// Reads bytes, never a `String`: a line with invalid UTF-8 must not terminate
/// the stream (the old `read_line(String)` path did, and its recovery branch
/// ran a lossy UTF-8 conversion, destroying bytes). SSL payload lines are
/// normalized to carry `data_b64`; a line that fails that check is reported
/// and skipped, never yielded in a corrupt state.
pub(crate) async fn read_json_line<R: AsyncBufRead + Unpin>(
    reader: &mut R,
    buffer: &mut Vec<u8>,
    line_number: usize,
    label: &str,
) -> std::io::Result<Option<JsonLine>> {
    buffer.clear();
    let read = reader.read_until(b'\n', buffer).await?;
    if read == 0 {
        return Ok(None);
    }

    let trimmed = trim_ascii(buffer);
    if trimmed.is_empty() {
        return Ok(Some(JsonLine::Skipped));
    }
    debug!("Line {}: {}", line_number, display_bytes(trimmed, 100));

    if !(trimmed.first() == Some(&b'{') && trimmed.last() == Some(&b'}')) {
        if contains_ascii_case_insensitive(trimmed, b"error")
            || contains_ascii_case_insensitive(trimmed, b"warn")
            || contains_ascii_case_insensitive(trimmed, b"failed")
        {
            log::warn!(
                "{}Possible error message at line {}: {}",
                label,
                line_number,
                display_bytes(trimmed, 200)
            );
        } else {
            log::warn!(
                "{}Skipping non-JSON line {}: {}",
                label,
                line_number,
                display_bytes(trimmed, 100)
            );
        }
        return Ok(Some(JsonLine::Skipped));
    }

    let mut value: serde_json::Value = match serde_json::from_slice(trimmed) {
        Ok(value) => value,
        Err(error) => {
            log::warn!(
                "{}Failed to parse JSON at line {}: {} - {}",
                label,
                line_number,
                error,
                display_bytes(trimmed, 200)
            );
            return Ok(Some(JsonLine::Skipped));
        }
    };

    // SSL payload lines get the authoritative byte field, including lines
    // replayed from old captures. A length mismatch means bytes were lost;
    // dropping the line is the only honest outcome.
    if value
        .as_object()
        .is_some_and(|object| object.contains_key("buf_size"))
        && let Err(error) = crate::framework::normalize::ensure_data_b64(&mut value, trimmed)
    {
        log::warn!("{}Dropping payload line {}: {}", label, line_number, error);
        return Ok(Some(JsonLine::Skipped));
    }

    Ok(Some(JsonLine::Value(value)))
}

// Supervisors own children independently of the consumer stream. Dropping a
// stream requests graceful shutdown while the supervisor continues draining.
static HELPER_STOP: std::sync::OnceLock<tokio::sync::watch::Sender<bool>> =
    std::sync::OnceLock::new();
static CAPTURE_HEALTH: std::sync::OnceLock<
    std::sync::Mutex<std::collections::BTreeMap<String, serde_json::Value>>,
> = std::sync::OnceLock::new();

fn helper_stop() -> &'static tokio::sync::watch::Sender<bool> {
    HELPER_STOP.get_or_init(|| tokio::sync::watch::channel(false).0)
}

pub fn request_helper_shutdown() {
    helper_stop().send_replace(true);
}

fn update_capture_health(key: &str, update: impl FnOnce(&mut serde_json::Value)) {
    let registry = CAPTURE_HEALTH.get_or_init(Default::default);
    if let Ok(mut registry) = registry.lock() {
        let row = registry.entry(key.into()).or_insert_with(|| {
            serde_json::json!({
                "started": true, "final_stats_seen": false, "diagnostics": {},
                "stdout_skipped": 0, "stdout_error": false, "stderr_error": false,
                "drain_complete": false
            })
        });
        update(row);
    }
}

fn source_capture_complete(name: &str, row: &serde_json::Value) -> bool {
    if row["started"] != true {
        return false;
    }
    let kind = name.split(':').next().unwrap_or("");
    if kind.eq_ignore_ascii_case("Process") && row["initial_snapshot_complete"] != true {
        return false;
    }
    if kind.eq_ignore_ascii_case("SSL") && !row["attached"].as_u64().is_some_and(|n| n > 0) {
        return false;
    }
    row["final_stats_seen"] == true
        && row["drain_complete"] == true
        && row["exit_status"] == 0
        && row["lost"].as_u64() == Some(0)
        && row["rate_limited"].as_u64().unwrap_or(0) == 0
        && row["forced_kill"] != true
        && row["stdout_skipped"].as_u64().unwrap_or(0) == 0
        && row["stdout_error"] != true
        && row["stderr_error"] != true
        && row["initial_snapshot_complete"] != false
        && row.get("attach_error").is_none()
        && row
            .get("attached")
            .is_none_or(|count| count.as_u64().is_some_and(|n| n > 0))
}

pub fn capture_health_snapshot() -> serde_json::Value {
    let sources = CAPTURE_HEALTH
        .get_or_init(Default::default)
        .lock()
        .map(|rows| rows.clone())
        .unwrap_or_default();
    let status = if sources.is_empty() {
        "not_configured"
    } else if sources
        .iter()
        .all(|(name, row)| source_capture_complete(name, row))
    {
        "healthy"
    } else {
        "unknown"
    };
    serde_json::json!({ "status": status, "sources": sources })
}

struct SupervisedJsonStream {
    inner: JsonStream,
    stop: Option<tokio::sync::oneshot::Sender<()>>,
}

impl Stream for SupervisedJsonStream {
    type Item = serde_json::Value;
    fn poll_next(
        self: Pin<&mut Self>,
        cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<Option<Self::Item>> {
        self.get_mut().inner.as_mut().poll_next(cx)
    }
}

impl Drop for SupervisedJsonStream {
    fn drop(&mut self) {
        if let Some(stop) = self.stop.take() {
            let _ = stop.send(());
        }
    }
}

/// Common binary executor for runners - now supports streaming
pub struct BinaryExecutor {
    binary_path: String,
    additional_args: Vec<String>,
    runner_name: Option<String>,
}

impl BinaryExecutor {
    pub fn new(binary_path: String) -> Self {
        Self {
            binary_path,
            additional_args: Vec::new(),
            runner_name: None,
        }
    }

    /// Add additional command-line arguments
    pub fn with_args(mut self, args: &[String]) -> Self {
        self.additional_args = args.to_vec();
        self
    }

    /// Set runner name for debugging purposes
    pub fn with_runner_name(mut self, name: String) -> Self {
        self.runner_name = Some(name);
        self
    }

    /// Execute binary and get raw JSON stream
    pub async fn get_json_stream(&self) -> Result<JsonStream, RunnerError> {
        log::info!(
            "Executing binary: {} {:?}",
            self.binary_path,
            self.additional_args
        );
        let mut child = TokioCommand::new(&self.binary_path)
            .args(&self.additional_args)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .spawn()?;
        let stdout = child.stdout.take().ok_or("missing helper stdout")?;
        let stderr = child.stderr.take().ok_or("missing helper stderr")?;
        let name = self
            .runner_name
            .clone()
            .unwrap_or_else(|| self.binary_path.clone());
        let key = format!("{}:{}", name, child.id().unwrap_or(0));
        update_capture_health(&key, |_| {});
        // Decouple helper stdout draining from downstream analyzer cost.
        // Capacity 256 blocked the reader while a debounced runtime trigger
        // ran (p50 0.65 s, max 6.1 s), the helper stopped draining its ring,
        // and a real 140 s agent run dropped 10.5k TLS events. Observer-on
        // triggers additionally await LLM reviews (29 calls took most of a
        // 128 s run), so the queue must absorb a multi-second stall. Bounded,
        // so sustained overload still surfaces as ring drops.
        const JSON_QUEUE_CAPACITY: usize = 65536;
        let (tx, mut rx) = tokio::sync::mpsc::channel(JSON_QUEUE_CAPACITY);
        let (stop_tx, mut stop_rx) = tokio::sync::oneshot::channel();
        let mut global_stop = helper_stop().subscribe();
        let stdout_tx = tx.clone();
        let stdout_name = name.clone();
        let stdout_key = key.clone();
        let stdout_task = tokio::spawn(async move {
            let mut reader = BufReader::new(stdout);
            let mut buffer = Vec::new();
            let mut line_number = 0;
            loop {
                line_number += 1;
                match read_json_line(&mut reader, &mut buffer, line_number, &stdout_name).await {
                    Ok(Some(JsonLine::Value(value))) => {
                        if value["direction"] == "STATS" {
                            update_capture_health(&stdout_key, |row| {
                                row["final_stats_seen"] = true.into();
                                row["lost"] = value["dropped_events"].clone();
                                row["rate_limited"] = 0.into();
                            });
                        }
                        // A dropped consumer must not prevent the helper from
                        // flushing stdout and reaching its final statistics.
                        let _ = stdout_tx.send(value).await;
                    }
                    Ok(Some(JsonLine::Skipped)) => {
                        if !trim_ascii(&buffer).is_empty() {
                            update_capture_health(&stdout_key, |row| {
                                row["stdout_skipped"] =
                                    (row["stdout_skipped"].as_u64().unwrap_or(0) + 1).into();
                            });
                        }
                    }
                    Ok(None) => break,
                    Err(error) => {
                        log::warn!("{} stdout: {}", stdout_name, error);
                        update_capture_health(&stdout_key, |row| row["stdout_error"] = true.into());
                        break;
                    }
                }
            }
        });
        let stderr_key = key.clone();
        let stderr_task = tokio::spawn(async move {
            let mut reader = BufReader::new(stderr);
            let mut line = String::new();
            loop {
                line.clear();
                match reader.read_line(&mut line).await {
                    Ok(0) => break,
                    Ok(_) => {}
                    Err(error) => {
                        log::warn!("{} stderr: {}", name, error);
                        update_capture_health(&stderr_key, |row| row["stderr_error"] = true.into());
                        break;
                    }
                }
                log::info!("[{}] STDERR: {}", name, line.trim());
                if let Ok(diagnostic) = serde_json::from_str::<serde_json::Value>(line.trim()) {
                    let event = diagnostic["event"].as_str().unwrap_or("").to_owned();
                    update_capture_health(&stderr_key, |row| {
                        row["diagnostics"][&event] = diagnostic.clone();
                        if event == "PROCESS_SNAPSHOT_STATS" {
                            let records = diagnostic["records"].as_u64();
                            let emitted = diagnostic["emitted"].as_u64();
                            row["initial_snapshot_complete"] = (diagnostic["complete"]==true
                                && diagnostic["error"].as_i64()==Some(0)
                                && matches!((records,emitted),(Some(total),Some(selected)) if selected<=total)).into();
                        }
                        if event == "TLS_CAPTURE_PROBES" {
                            row["attached"] = diagnostic["attached"].clone();
                            if let Some(flag) = diagnostic.get("core_read") {
                                row["core_read"] = flag.clone();
                            }
                            if let Some(flag) = diagnostic.get("core_write") {
                                row["core_write"] = flag.clone();
                            }
                        }
                        if event == "TLS_CAPTURE_ERROR" {
                            row["attach_error"] = diagnostic.clone();
                        }
                        if event == "TLS_RINGBUF_STATS" || event == "PROCESS_RINGBUF_STATS" {
                            row["final_stats_seen"] = true.into();
                            row["lost"] = diagnostic["dropped_events"].clone();
                            row["rate_limited"] = diagnostic
                                .get("rate_limit_drops")
                                .cloned()
                                .unwrap_or(0.into());
                            // Keep the per-cause split when the helper reports it.
                            // Old helpers only send the total; absence stays absent.
                            let reasons: serde_json::Map<String, serde_json::Value> = [
                                ("ringbuf_full", "ringbuf_full_dropped"),
                                ("pending_table", "pending_table_dropped"),
                                ("user_read", "user_read_dropped"),
                            ]
                            .iter()
                            .filter_map(|(label, field)| {
                                diagnostic
                                    .get(*field)
                                    .map(|value| ((*label).to_string(), value.clone()))
                            })
                            .collect();
                            if !reasons.is_empty() {
                                row["loss_reasons"] = serde_json::Value::Object(reasons);
                            }
                        }
                    });
                }
            }
        });
        tokio::spawn(async move {
            let status = tokio::select! {
                status = child.wait() => Some(status),
                _ = &mut stop_rx => None,
                _ = async {
                    if !*global_stop.borrow() { let _ = global_stop.changed().await; }
                } => None,
            };
            let status = if let Some(status) = status {
                status
            } else {
                if let Some(pid) = child.id() {
                    let _ = TokioCommand::new("kill")
                        .args(["-TERM", &pid.to_string()])
                        .status()
                        .await;
                }
                match tokio::time::timeout(std::time::Duration::from_secs(2), child.wait()).await {
                    Ok(status) => status,
                    Err(_) => {
                        update_capture_health(&key, |row| row["forced_kill"] = true.into());
                        let _ = child.kill().await;
                        child.wait().await
                    }
                }
            };
            update_capture_health(&key, |row| {
                row["exit_status"] = status
                    .ok()
                    .and_then(|status| status.code())
                    .map(serde_json::Value::from)
                    .unwrap_or(serde_json::Value::Null);
            });
            // These readers continue after the consumer drops, preventing
            // broken pipes during final flush. Their output is bounded by rx.
            let stdout_done = stdout_task.await.is_ok();
            let stderr_done = stderr_task.await.is_ok();
            update_capture_health(&key, |row| {
                row["drain_complete"] = (stdout_done && stderr_done).into()
            });
            let _ = tx.send(serde_json::json!({ "event": "CAPTURE_HEALTH", "health": capture_health_snapshot() })).await;
        });
        let stream = async_stream::stream! {
            while let Some(value) = rx.recv().await { yield value; }
        };
        Ok(Box::pin(SupervisedJsonStream {
            inner: Box::pin(stream),
            stop: Some(stop_tx),
        }))
    }
}

/// Common analyzer processor for runners
pub struct AnalyzerProcessor;

impl AnalyzerProcessor {
    /// Process events through a chain of analyzers
    pub async fn process_through_analyzers(
        mut stream: EventStream,
        analyzers: &mut [Box<dyn Analyzer>],
    ) -> Result<EventStream, RunnerError> {
        // Process through each analyzer in sequence
        for analyzer in analyzers.iter_mut() {
            stream = analyzer.process(stream).await?;
        }

        Ok(stream)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::BufReader;

    async fn read_all(input: &[u8]) -> Vec<serde_json::Value> {
        let mut reader = BufReader::new(input);
        let mut buffer = Vec::new();
        let mut values = Vec::new();
        let mut line_number = 0;
        while let Some(line) = read_json_line(&mut reader, &mut buffer, line_number + 1, "[test] ")
            .await
            .unwrap()
        {
            line_number += 1;
            if let JsonLine::Value(value) = line {
                values.push(value);
            }
        }
        values
    }

    #[test]
    fn source_health_requires_positive_source_specific_attachment_evidence() {
        let base = serde_json::json!({
            "started": true, "final_stats_seen": true, "drain_complete": true,
            "exit_status": 0, "lost": 0, "rate_limited": 0,
            "stdout_skipped": 0, "stdout_error": false, "stderr_error": false,
        });
        assert!(!source_capture_complete("Process:1", &base));
        assert!(!source_capture_complete("SSL:1", &base));
        let mut process = base.clone();
        process["initial_snapshot_complete"] = true.into();
        assert!(source_capture_complete("Process:1", &process));
        process["initial_snapshot_complete"] = serde_json::Value::Null;
        assert!(!source_capture_complete("Process:1", &process));
        let mut tls = base;
        tls["attached"] = 2.into();
        assert!(source_capture_complete("SSL:1", &tls));
        tls["started"] = false.into();
        assert!(!source_capture_complete("SSL:1", &tls));
    }

    #[tokio::test]
    async fn snapshot_health_requires_successful_transport_and_consistent_status() {
        use futures::StreamExt;
        for (complete, error, expected) in
            [(true, 0, true), (false, -95, false), (true, -22, false)]
        {
            let name = format!("snapshot-health-{error}");
            let stats = serde_json::json!({"event":"PROCESS_SNAPSHOT_STATS","complete":complete,"error":error,"records":2,"emitted":1});
            let args = vec![
                "-c".into(),
                r#"printf '%s\n' "$1" >&2"#.into(),
                "snapshot".into(),
                stats.to_string(),
            ];
            let mut stream = BinaryExecutor::new("/bin/sh".into())
                .with_runner_name(name.clone())
                .with_args(&args)
                .get_json_stream()
                .await
                .unwrap();
            while stream.next().await.is_some() {}
            let snapshot = capture_health_snapshot();
            let row = snapshot["sources"]
                .as_object()
                .unwrap()
                .iter()
                .find(|(key, _)| key.starts_with(&name))
                .unwrap()
                .1;
            assert_eq!(row["initial_snapshot_complete"], expected);
        }
    }

    #[tokio::test]
    async fn dropping_stream_gracefully_stops_and_drains_helper() {
        use futures::StreamExt;
        let dir = tempfile::tempdir().unwrap();
        let marker = dir.path().join("term-observed");
        let args = vec!["-c".into(),
            r#"trap 'echo stopped > "$1"; exit 0' TERM; echo '{"ready":true}'; while :; do sleep 0.02; done"#.into(),
            "capture-test".into(), marker.to_string_lossy().into_owned()];
        let executor = BinaryExecutor::new("/bin/sh".into()).with_args(&args);
        let mut stream = executor.get_json_stream().await.unwrap();
        assert_eq!(stream.next().await.unwrap()["ready"], true);
        drop(stream);
        tokio::time::timeout(std::time::Duration::from_secs(4), async {
            while !marker.exists() {
                tokio::time::sleep(std::time::Duration::from_millis(20)).await;
            }
        })
        .await
        .expect("helper must receive TERM before forced kill");
    }

    #[tokio::test]
    async fn helper_health_accounts_for_invalid_stdout_and_complete_drain() {
        use futures::StreamExt;
        let name = "capture-invalid-stdout";
        let args = vec!["-c".into(),
            "printf 'not-json\\n'; printf '%s\\n' '{\"event\":\"PROCESS_RINGBUF_STATS\",\"dropped_events\":0}' >&2".into()];
        let executor = BinaryExecutor::new("/bin/sh".into())
            .with_runner_name(name.into())
            .with_args(&args);
        let mut stream = executor.get_json_stream().await.unwrap();
        while stream.next().await.is_some() {}
        let snapshot = capture_health_snapshot();
        let row = snapshot["sources"]
            .as_object()
            .unwrap()
            .iter()
            .find(|(key, _)| key.starts_with(name))
            .unwrap()
            .1;
        assert_eq!(row["stdout_skipped"], 1);
        assert_eq!(row["drain_complete"], true);
        assert_eq!(row["final_stats_seen"], true);
        assert_ne!(snapshot["status"], "healthy");
    }

    #[tokio::test]
    async fn helper_health_preserves_per_cause_loss_split() {
        use futures::StreamExt;
        let name = "capture-loss-split";
        let args = vec!["-c".into(),
            "printf '%s\\n' '{\"event\":\"PROCESS_RINGBUF_STATS\",\"dropped_events\":9,\"ringbuf_full_dropped\":2,\"pending_table_dropped\":3,\"user_read_dropped\":4}' >&2".into()];
        let executor = BinaryExecutor::new("/bin/sh".into())
            .with_runner_name(name.into())
            .with_args(&args);
        let mut stream = executor.get_json_stream().await.unwrap();
        while stream.next().await.is_some() {}
        let snapshot = capture_health_snapshot();
        let row = snapshot["sources"]
            .as_object()
            .unwrap()
            .iter()
            .find(|(key, _)| key.starts_with(name))
            .unwrap()
            .1;
        assert_eq!(row["lost"], 9);
        assert_eq!(row["loss_reasons"]["ringbuf_full"], 2);
        assert_eq!(row["loss_reasons"]["pending_table"], 3);
        assert_eq!(row["loss_reasons"]["user_read"], 4);
    }

    #[tokio::test]
    async fn helper_health_preserves_global_failures_without_charging_outside_target() {
        use futures::StreamExt;
        let name = "capture-scoped-user-read";
        let stats = serde_json::json!({
            "event": "PROCESS_RINGBUF_STATS", "dropped_events": 0,
            "ringbuf_full_dropped": 0, "pending_table_dropped": 0,
            "user_read_dropped": 0, "global_user_read_failed": 7,
            "user_read_target": 0, "user_read_outside_target": 7,
            "user_read_unattributed": 0, "user_read_scope_consistent": true
        });
        let args = vec![
            "-c".into(),
            r#"printf '%s\n' "$1" >&2"#.into(),
            "fixture".into(),
            stats.to_string(),
        ];
        let executor = BinaryExecutor::new("/bin/sh".into())
            .with_runner_name(name.into())
            .with_args(&args);
        let mut stream = executor.get_json_stream().await.unwrap();
        while stream.next().await.is_some() {}
        let snapshot = capture_health_snapshot();
        let row = snapshot["sources"]
            .as_object()
            .unwrap()
            .iter()
            .find(|(key, _)| key.starts_with(name))
            .unwrap()
            .1;
        assert_eq!(row["lost"], 0);
        assert_eq!(row["loss_reasons"]["user_read"], 0);
        assert_eq!(row["final_stats_seen"], true);
        assert_eq!(row["drain_complete"], true);
        assert_eq!(row["diagnostics"]["PROCESS_RINGBUF_STATS"], stats);
    }

    #[tokio::test]
    async fn byte_reader_continues_after_a_non_utf8_line() {
        let mut input = b"{\"a\":1}\n".to_vec();
        input.extend_from_slice(&[0xFF, 0xFF]);
        input.push(b'\n');
        input.extend_from_slice(b"{\"b\":2}\n");

        let values = read_all(&input).await;
        assert_eq!(values.len(), 2);
        assert_eq!(values[0]["a"], 1);
        assert_eq!(values[1]["b"], 2);
    }

    #[tokio::test]
    async fn invalid_json_line_is_skipped_without_terminating() {
        let input = b"{\"a\":1}\n{not json}\n{\"b\":2}\n";
        let values = read_all(input).await;
        assert_eq!(values.len(), 2);
    }

    #[tokio::test]
    async fn legacy_payload_line_is_normalized_to_data_b64() {
        // Build the escape numerically so no editor/JSON layer rewrites it.
        let mut input = br#"{"buf_size":1,"data":""#.to_vec();
        input.extend_from_slice(&[0x5C, b'u', b'0', b'0', b'b', b'5']);
        input.extend_from_slice(br#""}"#);
        input.push(b'\n');

        let values = read_all(&input).await;
        assert_eq!(values.len(), 1);
        assert_eq!(values[0]["data_b64"], "tQ==");
        assert_eq!(values[0]["data_b64_source"], "legacy_unescape");
    }

    #[tokio::test]
    async fn payload_length_mismatch_drops_the_line() {
        let input = b"{\"buf_size\":4,\"data\":\"abc\"}\n{\"b\":2}\n";
        let values = read_all(input).await;
        assert_eq!(values.len(), 1, "corrupt payload line must not be yielded");
        assert_eq!(values[0]["b"], 2);
    }

    #[tokio::test]
    async fn eof_without_newline_is_parsed() {
        let values = read_all(b"{\"a\":1}").await;
        assert_eq!(values.len(), 1);
        assert_eq!(values[0]["a"], 1);
    }
}
