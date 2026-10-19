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
        // Log the actual exec command with all arguments
        if self.additional_args.is_empty() {
            log::info!("Executing binary: {}", self.binary_path);
        } else {
            log::info!(
                "Executing binary: {} {}",
                self.binary_path,
                self.additional_args.join(" ")
            );
        }

        let mut cmd = TokioCommand::new(&self.binary_path);
        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());

        // Add additional arguments if any
        if !self.additional_args.is_empty() {
            cmd.args(&self.additional_args);
            debug!("Added arguments: {:?}", self.additional_args);
        }

        let mut child = cmd.spawn().map_err(|e| {
            Box::new(std::io::Error::other(format!(
                "Failed to start binary: {}",
                e
            ))) as RunnerError
        })?;

        let stdout = child.stdout.take().ok_or_else(|| {
            Box::new(std::io::Error::other("Failed to get stdout")) as RunnerError
        })?;

        let stderr = child.stderr.take().ok_or_else(|| {
            Box::new(std::io::Error::other("Failed to get stderr")) as RunnerError
        })?;

        if let Some(pid) = child.id() {
            debug!("Binary started with PID: Some({})", pid);
        }

        // Clone needed data for the stream
        let runner_name = self.runner_name.clone();
        let binary_path = self.binary_path.clone();

        // Spawn a task to read and log stderr
        let stderr_runner_name = runner_name.clone();
        let stderr_binary_path = binary_path.clone();
        tokio::spawn(async move {
            let mut stderr_reader = BufReader::new(stderr);
            let mut stderr_line = String::new();

            loop {
                stderr_line.clear();
                match stderr_reader.read_line(&mut stderr_line).await {
                    Ok(0) => {
                        // EOF reached
                        break;
                    }
                    Ok(_) => {
                        let trimmed = stderr_line.trim();
                        if !trimmed.is_empty() {
                            // Log stderr output as ERROR for visibility
                            let runner_info = stderr_runner_name
                                .as_ref()
                                .map(|name| format!("[{}] ", name))
                                .unwrap_or_else(|| {
                                    format!(
                                        "[{}] ",
                                        std::path::Path::new(&stderr_binary_path)
                                            .file_name()
                                            .and_then(|n| n.to_str())
                                            .unwrap_or("unknown")
                                    )
                                });

                            // Check severity of the message
                            if trimmed.contains("Failed")
                                || trimmed.contains("Error")
                                || trimmed.contains("cannot")
                                || trimmed.contains("permission denied")
                            {
                                log::error!("{}STDERR: {}", runner_info, trimmed);
                            } else if trimmed.contains("warn") || trimmed.contains("Warning") {
                                log::warn!("{}STDERR: {}", runner_info, trimmed);
                            } else {
                                log::info!("{}STDERR: {}", runner_info, trimmed);
                            }
                        }
                    }
                    Err(e) => {
                        if e.kind() != std::io::ErrorKind::UnexpectedEof {
                            log::warn!("Error reading stderr: {}", e);
                        }
                        break;
                    }
                }
            }
        });

        let stream = async_stream::stream! {
            let mut reader = BufReader::new(stdout);
            let mut buffer: Vec<u8> = Vec::new();
            let mut line_count: usize = 0;
            let label = runner_name
                .as_ref()
                .map(|name| format!("[{}] ", name))
                .unwrap_or_else(|| {
                    format!(
                        "[{}] ",
                        std::path::Path::new(&binary_path)
                            .file_name()
                            .and_then(|name| name.to_str())
                            .unwrap_or("unknown")
                    )
                });

            debug!("Reading from binary stdout");

            loop {
                match read_json_line(&mut reader, &mut buffer, line_count + 1, &label).await {
                    Ok(Some(JsonLine::Value(value))) => {
                        line_count += 1;
                        yield value;
                    }
                    Ok(Some(JsonLine::Skipped)) => {
                        line_count += 1;
                    }
                    Ok(None) => {
                        debug!("Binary stdout closed (EOF)");
                        break;
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {
                        debug!("Read interrupted, retrying...");
                        continue;
                    }
                    Err(error) => {
                        log::warn!(
                            "{}Error reading from binary: {} (kind: {:?})",
                            label,
                            error,
                            error.kind()
                        );
                        break;
                    }
                }
            }

            log::info!("Terminating binary process");

            // Terminate the child process
            if let Err(e) = child.kill().await {
                log::warn!("Failed to kill binary process: {}", e);
            }

            // Wait for process to finish
            match child.wait().await {
                Ok(status) => {
                    debug!("Binary process terminated with status: {}", status);
                }
                Err(e) => {
                    log::warn!("Error waiting for binary process: {}", e);
                }
            }
        };

        Ok(Box::pin(stream))
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
