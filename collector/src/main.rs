// SPDX-License-Identifier: MIT
// Copyright (c) 2026 eunomia-bpf org.

use clap::{Parser, Subcommand};
use futures::stream::StreamExt;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
use tokio::signal;
use tokio::sync::broadcast;

mod responsibility;
mod framework;
mod server;

#[allow(unused_imports)]
use framework::analyzers::file_access_audit::FileAccessAuditAnalyzer;
#[allow(unused_imports)]
use framework::analyzers::file_access_audit_contract::FileAccessAuditConfig;

use framework::{
    analyzers::{
        AuthHeaderRemover, ConnectionBuffer, FileLogger, HTTPFilter,
        SecuritySignoffAnalyzer, SecuritySignoffConfig,
        HTTPParser, HandoffInjectionAnalyzer, MultiAgentAnomalyAnalyzer, OutputAnalyzer,
        SSEProcessor, SSLFilter,
        TimestampNormalizer, print_global_http_filter_metrics,
        print_global_ssl_filter_metrics,
    },
    binary_extractor::BinaryExtractor,
    runners::{
        AgentRunner, ProcessRunner, Runner, RunnerError, SslRunner, StdioRunner, SystemRunner,
    },
};

use crate::responsibility::runtime::runtime::{ResponsibilityRuntime, RuntimeConfig};
use crate::responsibility::persistence::PersistenceWriter;
use crate::responsibility::types::alert_types::ResponsibilityAlert;

use server::WebServer;

static SHUTDOWN_REQUESTED: AtomicBool = AtomicBool::new(false);

fn convert_runner_error(e: RunnerError) -> Box<dyn std::error::Error + Send + Sync> {
    Box::new(std::io::Error::new(
        std::io::ErrorKind::Other,
        e.to_string(),
    ))
}

async fn setup_signal_handler() {
    let mut sigint = signal::unix::signal(signal::unix::SignalKind::interrupt())
        .expect("Failed to install SIGINT handler");

    tokio::spawn(async move {
        sigint.recv().await;
        println!("\n\nReceived SIGINT, shutting down...");

        // Signal the ResponsibilityRuntime to trigger Phase 2 finalization
        crate::responsibility::runtime::runtime::RUNTIME_SHUTDOWN.store(true, Ordering::Relaxed);

        // Print HTTP filter metrics using the global function
        print_global_http_filter_metrics();

        // Print SSL filter metrics using the global function
        print_global_ssl_filter_metrics();

        SHUTDOWN_REQUESTED.store(true, Ordering::Relaxed);
    });
}

/// Returns a future that resolves when shutdown is requested via SIGINT.
async fn shutdown_requested() {
    loop {
        if SHUTDOWN_REQUESTED.load(Ordering::Relaxed) {
            return;
        }
        tokio::time::sleep(Duration::from_millis(200)).await;
    }
}

#[derive(Parser)]
#[command(author, version, about, long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(clap::ValueEnum, Clone, Copy, Debug)]
enum SemanticObserverModeArg {
    Heuristic,
    Sidecar,
}

#[derive(Subcommand)]
enum Commands {
    /// Analyze SSL traffic with raw JSON output
    Ssl {
        /// Enable SSE processing for SSL traffic
        #[arg(long)]
        sse_merge: bool,
        /// Enable HTTP parsing (automatically enables SSE merge first)
        #[arg(long)]
        http_parser: bool,
        /// Include raw SSL data in HTTP parser events
        #[arg(long)]
        http_raw_data: bool,
        /// HTTP filter patterns to exclude events (can be used multiple times)
        #[arg(long)]
        http_filter: Vec<String>,
        /// Disable authorization header removal from HTTP traffic
        #[arg(long)]
        disable_auth_removal: bool,
        /// SSL filter patterns to exclude events (can be used multiple times)
        #[arg(long)]
        ssl_filter: Vec<String>,
        /// Suppress console output
        #[arg(short, long)]
        quiet: bool,
        /// Enable log rotation
        #[arg(long)]
        rotate_logs: bool,
        /// Maximum log file size in MB (used with --rotate-logs)
        #[arg(long, default_value = "10")]
        max_log_size: u64,
        /// Start web server on port 7395
        #[arg(long)]
        server: bool,
        /// Server port (used with --server)
        #[arg(long, default_value = "7395")]
        server_port: u16,
        /// Log file to serve via API (used with --server)
        #[arg(long, default_value = "ssl.log")]
        log_file: String,
        /// Path to the ELF binary executable to monitor (e.g., ~/.nvm/versions/node/v20.0.0/bin/node)
        #[arg(long)]
        binary_path: Option<String>,
        /// Additional arguments to pass to the SSL binary
        #[arg(last = true)]
        args: Vec<String>,
    },
    /// Test process runner with embedded binary
    Process {
        /// Suppress console output
        #[arg(short, long)]
        quiet: bool,
        /// Enable log rotation
        #[arg(long)]
        rotate_logs: bool,
        /// Maximum log file size in MB (used with --rotate-logs)
        #[arg(long, default_value = "10")]
        max_log_size: u64,
        /// Start web server on port 7395
        #[arg(long)]
        server: bool,
        /// Server port (used with --server)
        #[arg(long, default_value = "7395")]
        server_port: u16,
        /// Log file to serve via API (used with --server)
        #[arg(long, default_value = "process.log")]
        log_file: String,
        /// Additional arguments to pass to the process binary
        #[arg(last = true)]
        args: Vec<String>,
    },
    /// Capture local stdio payloads from a target process
    Stdio {
        /// Target PID (required)
        #[arg(short = 'p', long)]
        pid: u32,
        /// Filter by UID
        #[arg(short = 'u', long)]
        uid: Option<u32>,
        /// Filter by command name
        #[arg(short = 'c', long)]
        comm: Option<String>,
        /// Capture all FDs instead of only stdin/stdout/stderr
        #[arg(long)]
        all_fds: bool,
        /// Maximum bytes captured per event
        #[arg(long, default_value = "8192")]
        max_bytes: u32,
        /// Suppress console output
        #[arg(short, long)]
        quiet: bool,
        /// Enable log rotation
        #[arg(long)]
        rotate_logs: bool,
        /// Maximum log file size in MB (used with --rotate-logs)
        #[arg(long, default_value = "10")]
        max_log_size: u64,
        /// Start web server on port 7395
        #[arg(long)]
        server: bool,
        /// Server port (used with --server)
        #[arg(long, default_value = "7395")]
        server_port: u16,
        /// Log file to serve via API (used with --server)
        #[arg(long, default_value = "stdio.log")]
        log_file: String,
    },
    /// Combined SSL and Process monitoring with configurable options
    Trace {
        /// Enable SSL monitoring
        #[arg(long, action = clap::ArgAction::Set, default_value = "true")]
        ssl: bool,
        /// SSL filter by UID
        #[arg(long)]
        ssl_uid: Option<u32>,
        /// SSL filter patterns (for analyzer-level filtering)
        #[arg(long)]
        ssl_filter: Vec<String>,
        /// Show SSL handshake events
        #[arg(long)]
        ssl_handshake: bool,
        /// Enable HTTP parsing for SSL
        #[arg(long, action = clap::ArgAction::Set, default_value = "true")]
        ssl_http: bool,
        /// Include raw SSL data in HTTP parser events
        #[arg(long)]
        ssl_raw_data: bool,

        /// Enable process monitoring
        #[arg(long, action = clap::ArgAction::Set, default_value = "true")]
        process: bool,
        /// Enable stdio payload monitoring (requires --pid)
        #[arg(long, requires = "pid")]
        stdio: bool,
        /// Stdio filter by UID
        #[arg(long)]
        stdio_uid: Option<u32>,
        /// Stdio filter by command name
        #[arg(long)]
        stdio_comm: Option<String>,
        /// Capture all FDs for stdio monitoring instead of only 0/1/2
        #[arg(long)]
        stdio_all_fds: bool,
        /// Maximum bytes captured per stdio event
        #[arg(long, default_value = "8192")]
        stdio_max_bytes: u32,
        /// Process command filter (comma-separated list)
        #[arg(short = 'c', long)]
        comm: Option<String>,
        /// Process PID filter
        #[arg(short = 'p', long)]
        pid: Option<u32>,
        /// Comma-separated process PID set filter
        #[arg(long, value_delimiter = ',')]
        pid_set: Vec<u32>,
        /// Process duration filter (minimum duration in ms)
        #[arg(long)]
        duration: Option<u32>,
        /// Process filtering mode (0=all, 1=proc, 2=filter)
        #[arg(long)]
        mode: Option<u32>,

        /// Enable system resource monitoring (CPU and memory)
        #[arg(long)]
        system: bool,
        /// System monitoring interval in seconds
        #[arg(long, default_value = "2")]
        system_interval: u64,

        /// HTTP filters (applied to SSL runner after HTTP parsing)
        #[arg(long)]
        http_filter: Vec<String>,
        /// Disable authorization header removal from HTTP traffic
        #[arg(long)]
        disable_auth_removal: bool,
        /// Disable responsibility-layer Observer LLM (→ heuristic fallback, for ablation A4)
        #[arg(long)]
        disable_responsibility_observer: bool,
        /// Path to the ELF binary executable to monitor (e.g., ~/.nvm/versions/node/v20.0.0/bin/node)
        #[arg(long)]
        binary_path: Option<String>,
        /// Log file for output and server
        #[arg(short = 'o', long, default_value = "trace.log")]
        log_file: String,
        /// Suppress console output
        #[arg(short, long)]
        quiet: bool,
        /// Enable log rotation
        #[arg(long)]
        rotate_logs: bool,
        /// Maximum log file size in MB (used with --rotate-logs)
        #[arg(long, default_value = "10")]
        max_log_size: u64,
        /// Start web server on port 7395
        #[arg(long)]
        server: bool,
        /// Server port (used with --server)
        #[arg(long, default_value = "7395")]
        server_port: u16,
    },
    /// Record agent activity with optimized filters and settings
    /// Equivalent to: trace -c claude --http-filter "request.path_prefix=/v1/rgstr | response.status_code=202 | request.method=HEAD | response.body=" --ssl-filter "data=0\\r\\n\\r\\n|data.type=binary" -q --server-port 7395 --server -o record.log
    Record {
        /// Process command filter (defaults to "claude")
        #[arg(short = 'c', long)]
        comm: String,
        /// Path to the ELF binary executable to monitor (e.g., ~/.nvm/versions/node/v20.0.0/bin/node)
        #[arg(long)]
        binary_path: Option<String>,
        /// Log file for output and server
        #[arg(short = 'o', long, default_value = "record.log")]
        log_file: String,
        /// Enable log rotation
        #[arg(long, default_value = "true")]
        rotate_logs: bool,
        /// Maximum log file size in MB (used with --rotate-logs)
        #[arg(long, default_value = "10")]
        max_log_size: u64,
        /// Server port (used with --server, always enabled)
        #[arg(long, default_value = "7395")]
        server_port: u16,
    },
    /// Monitor system resources (CPU and memory)
    System {
        /// Monitoring interval in seconds
        #[arg(short = 'i', long, default_value = "2")]
        interval: u64,
        /// Process PID to monitor
        #[arg(short = 'p', long)]
        pid: Option<u32>,
        /// Process command name to monitor
        #[arg(short = 'c', long)]
        comm: Option<String>,
        /// Exclude children processes from aggregation
        #[arg(long)]
        no_children: bool,
        /// CPU usage threshold for alerts (%)
        #[arg(long)]
        cpu_threshold: Option<f64>,
        /// Memory usage threshold for alerts (MB)
        #[arg(long)]
        memory_threshold: Option<u64>,
        /// Log file for output and server
        #[arg(short = 'o', long, default_value = "system.log")]
        log_file: String,
        /// Suppress console output
        #[arg(short, long)]
        quiet: bool,
        /// Enable log rotation
        #[arg(long)]
        rotate_logs: bool,
        /// Maximum log file size in MB (used with --rotate-logs)
        #[arg(long, default_value = "10")]
        max_log_size: u64,
        /// Start web server on port 7395
        #[arg(long)]
        server: bool,
        /// Server port (used with --server)
        #[arg(long, default_value = "7395")]
        server_port: u16,
    },
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    // Initialize env_logger with default log level of info
    env_logger::Builder::from_default_env()
        .filter_level(log::LevelFilter::Info)
        .init();

    let cli = Cli::parse();

    // Setup signal handler for graceful shutdown
    setup_signal_handler().await;

    // Create BinaryExtractor with embedded binaries
    let binary_extractor = BinaryExtractor::new().await?;

    match &cli.command {
        Commands::Ssl {
            sse_merge,
            http_parser,
            http_raw_data,
            http_filter,
            disable_auth_removal,
            ssl_filter,
            quiet,
            rotate_logs,
            max_log_size,
            server,
            server_port,
            log_file,
            binary_path,
            args,
        } => run_raw_ssl(
            &binary_extractor,
            *sse_merge,
            *http_parser,
            *http_raw_data,
            http_filter,
            *disable_auth_removal,
            ssl_filter,
            *quiet,
            *rotate_logs,
            *max_log_size,
            *server,
            *server_port,
            log_file,
            binary_path.as_deref(),
            args,
        )
        .await
        .map_err(convert_runner_error)?,
        Commands::Process {
            quiet,
            rotate_logs,
            max_log_size,
            server,
            server_port,
            log_file,
            args,
        } => run_raw_process(
            &binary_extractor,
            *quiet,
            *rotate_logs,
            *max_log_size,
            *server,
            *server_port,
            log_file,
            args,
        )
        .await
        .map_err(convert_runner_error)?,
        Commands::Stdio {
            pid,
            uid,
            comm,
            all_fds,
            max_bytes,
            quiet,
            rotate_logs,
            max_log_size,
            server,
            server_port,
            log_file,
        } => run_raw_stdio(
            &binary_extractor,
            *pid,
            *uid,
            comm.as_deref(),
            *all_fds,
            *max_bytes,
            *quiet,
            *rotate_logs,
            *max_log_size,
            *server,
            *server_port,
            log_file,
        )
        .await
        .map_err(convert_runner_error)?,
        Commands::Trace {
            ssl,
            ssl_uid,
            pid,
            pid_set,
            comm,
            ssl_filter,
            ssl_handshake,
            ssl_http,
            ssl_raw_data,
            process,
            stdio,
            stdio_uid,
            stdio_comm,
            stdio_all_fds,
            stdio_max_bytes,
            duration,
            mode,
            system,
            system_interval,
            http_filter,
            disable_auth_removal,
            disable_responsibility_observer,
            binary_path,
            log_file,
            quiet,
            rotate_logs,
            max_log_size,
            server,
            server_port,
        } => run_trace(
            &binary_extractor,
            *ssl,
            *pid,
            pid_set,
            *ssl_uid,
            comm.as_deref(),
            ssl_filter,
            *ssl_handshake,
            *ssl_http,
            *ssl_raw_data,
            *process,
            *stdio,
            *stdio_uid,
            stdio_comm.as_deref(),
            *stdio_all_fds,
            *stdio_max_bytes,
            *duration,
            *mode,
            *system,
            *system_interval,
            http_filter,
            *disable_auth_removal,
            *disable_responsibility_observer,
            binary_path.as_deref(),
            log_file,
            *quiet,
            *rotate_logs,
            *max_log_size,
            *server,
            *server_port,
        )
        .await
        .map_err(convert_runner_error)?,
        Commands::Record {
            comm,
            binary_path,
            log_file,
            rotate_logs,
            max_log_size,
            server_port,
        } => {
            // Predefined filter patterns optimized for agent monitoring
            let http_filter_patterns = vec![
                "request.path_prefix=/v1/rgstr | response.status_code=202 | request.method=HEAD | response.body=".to_string(),
            ];
            let ssl_filter_patterns = vec!["data=0\\r\\n\\r\\n | data.type=binary".to_string()];

            // Enable system monitoring by default for record command
            run_trace(
                &binary_extractor,
                true,
                None,
                &[],
                None,
                Some(comm),
                &ssl_filter_patterns,
                false,
                true,
                false,
                true,
                false,
                None,
                None,
                false,
                8192,
                None,
                None,
                true,
                2,
                &http_filter_patterns,
                false,
                false,
                binary_path.as_deref(),
                log_file,
                true,
                *rotate_logs,
                *max_log_size,
                true,
                *server_port,
            )
            .await
            .map_err(convert_runner_error)?
        }
        Commands::System {
            interval,
            pid,
            comm,
            no_children,
            cpu_threshold,
            memory_threshold,
            log_file,
            quiet,
            rotate_logs,
            max_log_size,
            server,
            server_port,
        } => run_system(
            *interval,
            *pid,
            comm.as_deref(),
            !*no_children,
            *cpu_threshold,
            *memory_threshold,
            log_file,
            *quiet,
            *rotate_logs,
            *max_log_size,
            *server,
            *server_port,
        )
        .await
        .map_err(convert_runner_error)?,
    }

    Ok(())
}

fn resolve_pid_targets(pid: Option<u32>, pid_set: &[u32]) -> Vec<u32> {
    if !pid_set.is_empty() {
        return pid_set.to_vec();
    }
    pid.into_iter().collect()
}

fn is_elf_binary(path: &Path) -> bool {
    let mut file = match std::fs::File::open(path) {
        Ok(f) => f,
        Err(_) => return false,
    };

    let mut magic = [0u8; 4];
    if file.read_exact(&mut magic).is_err() {
        return false;
    }

    magic == [0x7f, b'E', b'L', b'F']
}

fn detect_launcher_runtime(path: &Path) -> Option<&'static str> {
    let mut file = std::fs::File::open(path).ok()?;
    let mut buf = [0u8; 256];
    let n = file.read(&mut buf).ok()?;
    if n == 0 {
        return None;
    }

    let content = String::from_utf8_lossy(&buf[..n]);
    let first_line = content.lines().next()?;
    if !first_line.starts_with("#!") {
        return None;
    }

    if first_line.contains("node") {
        Some("node")
    } else if first_line.contains("bun") {
        Some("bun")
    } else {
        None
    }
}

fn find_executable_in_path(name: &str) -> Option<PathBuf> {
    let path_env = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path_env) {
        let candidate = dir.join(name);
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

fn resolve_ssl_binary_path(binary_path: Option<&str>) -> Option<String> {
    let requested = binary_path?;
    let requested_path = PathBuf::from(requested);
    let canonical_path = std::fs::canonicalize(&requested_path).unwrap_or(requested_path);

    if is_elf_binary(&canonical_path) {
        return Some(canonical_path.to_string_lossy().to_string());
    }

    if let Some(runtime_name) = detect_launcher_runtime(&canonical_path) {
        if let Some(runtime_path) = find_executable_in_path(runtime_name) {
            let runtime_path = std::fs::canonicalize(&runtime_path).unwrap_or(runtime_path);
            if is_elf_binary(&runtime_path) {
                println!(
                    "! --binary-path '{}' is a launcher script, using runtime ELF '{}' instead",
                    canonical_path.display(),
                    runtime_path.display()
                );
                return Some(runtime_path.to_string_lossy().to_string());
            }
        }

        println!(
            "! --binary-path '{}' is a {} launcher script, but no ELF '{}' executable was resolved from PATH",
            canonical_path.display(),
            runtime_name,
            runtime_name
        );
    } else {
        println!(
            "! --binary-path '{}' is not an ELF executable",
            canonical_path.display()
        );
    }

    println!(
        "! Continuing without --binary-path. If SSL capture is empty, pass a real ELF runtime binary (for example: node/bun executable)."
    );
    None
}

/// Show raw SSL events as JSON with optional chunk merging and HTTP parsing
async fn run_raw_ssl(
    binary_extractor: &BinaryExtractor,
    enable_chunk_merger: bool,
    enable_http_parser: bool,
    include_raw_data: bool,
    http_filter_patterns: &Vec<String>,
    disable_auth_removal: bool,
    ssl_filter_patterns: &Vec<String>,
    quiet: bool,
    rotate_logs: bool,
    max_log_size: u64,
    enable_server: bool,
    server_port: u16,
    log_file: &str,
    binary_path: Option<&str>,
    args: &Vec<String>,
) -> Result<(), RunnerError> {
    println!("Raw SSL Events");
    println!("{}", "=".repeat(60));

    let mut ssl_runner = SslRunner::from_binary_extractor(binary_extractor.get_tls_capture_path());

    // Set up event broadcasting for server if enabled
    let (event_sender, _event_receiver) = broadcast::channel(1000);

    let resolved_binary_path = resolve_ssl_binary_path(binary_path);

    // Build arguments list with binary_path if provided
    let mut final_args = Vec::new();
    if let Some(path) = resolved_binary_path.as_deref() {
        final_args.push("--binary-path".to_string());
        final_args.push(path.to_string());
    }
    final_args.extend_from_slice(args);

    // Add all arguments if we have any
    if !final_args.is_empty() {
        ssl_runner = ssl_runner.with_args(&final_args);
    }

    // Add TimestampNormalizer first to convert nanoseconds since boot to milliseconds since epoch
    ssl_runner = ssl_runner.add_analyzer(Box::new(TimestampNormalizer::new()));

    // Add SSL filter if patterns are provided
    if !ssl_filter_patterns.is_empty() {
        ssl_runner = ssl_runner.add_analyzer(Box::new(SSLFilter::with_patterns(
            ssl_filter_patterns.clone(),
        )));
    }

    // ConnectionBuffer: assemble SSL_write fragments before downstream parsing
    ssl_runner = ssl_runner.add_analyzer(Box::new(ConnectionBuffer::new()));

    // Add analyzers based on flags - when HTTP parser is enabled, always enable SSE merge first
    if enable_http_parser {
        ssl_runner = ssl_runner.add_analyzer(Box::new(SSEProcessor::new_with_timeout(30000)));

        // Create HTTP parser with appropriate configuration
        let http_parser = if include_raw_data {
            HTTPParser::new()
        } else {
            HTTPParser::new().disable_raw_data()
        };
        ssl_runner = ssl_runner.add_analyzer(Box::new(http_parser));

        // Add HTTP filter if patterns are provided
        if !http_filter_patterns.is_empty() {
            ssl_runner = ssl_runner.add_analyzer(Box::new(HTTPFilter::with_patterns(
                http_filter_patterns.clone(),
            )));
        }

        // Add authorization header remover by default (unless disabled)
        if !disable_auth_removal {
            ssl_runner = ssl_runner.add_analyzer(Box::new(AuthHeaderRemover::new()));
        }

        let raw_data_info = if include_raw_data {
            " (with raw data)"
        } else {
            ""
        };
        let ssl_filter_info = if !ssl_filter_patterns.is_empty() {
            " with SSL filtering,"
        } else {
            ""
        };
        let http_filter_info = if !http_filter_patterns.is_empty() {
            " and HTTP filtering"
        } else {
            ""
        };
        println!(
            "Starting SSL event stream{} with SSE processing, HTTP parsing{}{} enabled (press Ctrl+C to stop):",
            ssl_filter_info, raw_data_info, http_filter_info
        );
    } else if enable_chunk_merger {
        ssl_runner = ssl_runner.add_analyzer(Box::new(SSEProcessor::new_with_timeout(30000)));
        let ssl_filter_info = if !ssl_filter_patterns.is_empty() {
            " with SSL filtering and"
        } else {
            " with"
        };
        println!(
            "Starting SSL event stream{} SSE processing enabled (press Ctrl+C to stop):",
            ssl_filter_info
        );
    } else {
        let ssl_filter_info = if !ssl_filter_patterns.is_empty() {
            " with SSL filtering and"
        } else {
            " with"
        };
        println!(
            "Starting SSL event stream{} raw JSON output (press Ctrl+C to stop):",
            ssl_filter_info
        );
    }

    ssl_runner = ssl_runner.add_analyzer(Box::new(if rotate_logs {
        FileLogger::with_max_size(log_file, max_log_size).unwrap()
    } else {
        FileLogger::new(log_file).unwrap()
    }));

    if !quiet {
        ssl_runner = ssl_runner.add_analyzer(Box::new(OutputAnalyzer::new()));
    }

    // Start web server if enabled
    let _server_handle = start_web_server_if_enabled(
        enable_server,
        server_port,
        Some(log_file),
        event_sender.clone(),
    )
    .await
    .map_err(|e| RunnerError::from(format!("Failed to start server: {}", e)))?;

    let mut stream = ssl_runner.run().await?;

    // Consume the stream to actually process events
    loop {
        tokio::select! {
            event = stream.next() => {
                match event {
                    Some(event) => {
                        if enable_server {
                            let _ = event_sender.send(event);
                        }
                    }
                    None => break,
                }
            }
            _ = shutdown_requested() => {
                println!("SSL runner shutting down gracefully...");
                break;
            }
        }
    }

    Ok(())
}

/// Show raw process events as JSON
async fn run_raw_process(
    binary_extractor: &BinaryExtractor,
    quiet: bool,
    rotate_logs: bool,
    max_log_size: u64,
    enable_server: bool,
    server_port: u16,
    log_file: &str,
    args: &Vec<String>,
) -> Result<(), RunnerError> {
    println!("Raw Process Events");
    println!("{}", "=".repeat(60));

    let mut process_runner =
        ProcessRunner::from_binary_extractor(binary_extractor.get_process_path());

    // Set up event broadcasting for server if enabled
    let (event_sender, _event_receiver) = broadcast::channel(1000);

    // Process runner uses the user-provided arguments directly.
    // The process eBPF binary supports: -m (mode), -c (commands), -p (pid), -d (duration), -v (verbose).
    // Removed unsupported --trace-fs/--trace-net/--trace-signals flags that caused
    // "unrecognized option" errors and prevented process event capture.
    process_runner = process_runner.with_args(args);

    // Add TimestampNormalizer first to convert nanoseconds since boot to milliseconds since epoch
    process_runner = process_runner.add_analyzer(Box::new(TimestampNormalizer::new()));

    if !quiet {
        process_runner = process_runner.add_analyzer(Box::new(OutputAnalyzer::new()));
    }

    process_runner = process_runner.add_analyzer(Box::new(if rotate_logs {
        FileLogger::with_max_size(log_file, max_log_size).unwrap()
    } else {
        FileLogger::new(log_file).unwrap()
    }));

    // Start web server if enabled
    let _server_handle = start_web_server_if_enabled(
        enable_server,
        server_port,
        Some(log_file),
        event_sender.clone(),
    )
    .await
    .map_err(|e| RunnerError::from(format!("Failed to start server: {}", e)))?;

    println!("Starting process event stream with raw JSON output (press Ctrl+C to stop):");
    let mut stream = process_runner.run().await?;

    // Consume the stream to actually process events
    loop {
        tokio::select! {
            event = stream.next() => {
                match event {
                    Some(event) => {
                        if enable_server {
                            let _ = event_sender.send(event);
                        }
                    }
                    None => break,
                }
            }
            _ = shutdown_requested() => {
                println!("Process runner shutting down gracefully...");
                break;
            }
        }
    }

    Ok(())
}

fn build_stdio_args(
    pid: u32,
    uid: Option<u32>,
    comm: Option<&str>,
    all_fds: bool,
    max_bytes: u32,
) -> Vec<String> {
    let mut args = vec!["-p".to_string(), pid.to_string()];

    if let Some(uid_filter) = uid {
        args.extend(["-u".to_string(), uid_filter.to_string()]);
    }
    if let Some(comm_filter) = comm {
        args.extend(["-c".to_string(), comm_filter.to_string()]);
    }
    if all_fds {
        args.push("--all-fds".to_string());
    }
    args.extend(["--max-bytes".to_string(), max_bytes.to_string()]);

    args
}

/// Show raw stdio events as JSON
async fn run_raw_stdio(
    binary_extractor: &BinaryExtractor,
    pid: u32,
    uid: Option<u32>,
    comm: Option<&str>,
    all_fds: bool,
    max_bytes: u32,
    quiet: bool,
    rotate_logs: bool,
    max_log_size: u64,
    enable_server: bool,
    server_port: u16,
    log_file: &str,
) -> Result<(), RunnerError> {
    println!("Raw Stdio Events");
    println!("{}", "=".repeat(60));

    let mut stdio_runner =
        StdioRunner::from_binary_extractor(binary_extractor.get_stdiocap_path()?);

    // Set up event broadcasting for server if enabled
    let (event_sender, _event_receiver) = broadcast::channel(1000);

    let stdio_args = build_stdio_args(pid, uid, comm, all_fds, max_bytes);
    stdio_runner = stdio_runner.with_args(&stdio_args);

    // Add TimestampNormalizer first to convert nanoseconds since boot to milliseconds since epoch
    stdio_runner = stdio_runner.add_analyzer(Box::new(TimestampNormalizer::new()));

    if !quiet {
        stdio_runner = stdio_runner.add_analyzer(Box::new(OutputAnalyzer::new()));
    }

    stdio_runner = stdio_runner.add_analyzer(Box::new(if rotate_logs {
        FileLogger::with_max_size(log_file, max_log_size).unwrap()
    } else {
        FileLogger::new(log_file).unwrap()
    }));

    // Start web server if enabled
    let _server_handle = start_web_server_if_enabled(
        enable_server,
        server_port,
        Some(log_file),
        event_sender.clone(),
    )
    .await
    .map_err(|e| RunnerError::from(format!("Failed to start server: {}", e)))?;

    println!(
        "Starting stdio event stream for PID {} (press Ctrl+C to stop):",
        pid
    );
    let mut stream = stdio_runner.run().await?;

    loop {
        tokio::select! {
            event = stream.next() => {
                match event {
                    Some(event) => {
                        if enable_server {
                            let _ = event_sender.send(event);
                        }
                    }
                    None => break,
                }
            }
            _ = shutdown_requested() => {
                println!("Stdio runner shutting down gracefully...");
                break;
            }
        }
    }

    Ok(())
}

/// Trace monitoring with configurable runners and analyzers
async fn run_trace(
    binary_extractor: &BinaryExtractor,
    ssl_enabled: bool,
    pid: Option<u32>,
    pid_set: &[u32],
    ssl_uid: Option<u32>,
    comm: Option<&str>,
    ssl_filter: &[String],
    ssl_handshake: bool,
    ssl_http: bool,
    ssl_raw_data: bool,
    process_enabled: bool,
    stdio_enabled: bool,
    stdio_uid: Option<u32>,
    stdio_comm: Option<&str>,
    stdio_all_fds: bool,
    stdio_max_bytes: u32,
    duration: Option<u32>,
    mode: Option<u32>,
    system_enabled: bool,
    system_interval: u64,
    http_filter: &[String],
    disable_auth_removal: bool,
    disable_responsibility_observer: bool,
    binary_path: Option<&str>,
    log_file: &str,
    quiet: bool,
    rotate_logs: bool,
    max_log_size: u64,
    enable_server: bool,
    server_port: u16,
) -> Result<(), RunnerError> {
    println!("Trace Monitoring");
    println!("{}", "=".repeat(60));

    // Set up event broadcasting for server if enabled
    let (event_sender, _event_receiver) = broadcast::channel(1000);

    let mut agent = AgentRunner::new("trace");
    let pid_targets = resolve_pid_targets(pid, pid_set);

    // Add SSL runner if enabled
    if ssl_enabled {
        let resolved_binary_path = resolve_ssl_binary_path(binary_path);
        let skip_ssl_comm_filter = binary_path.is_some();
        let ssl_targets: Vec<Option<u32>> = if pid_targets.is_empty() {
            vec![pid]
        } else {
            pid_targets.iter().copied().map(Some).collect()
        };

        for pid_target in ssl_targets {
            let mut ssl_runner =
                SslRunner::from_binary_extractor(binary_extractor.get_tls_capture_path());
            let mut ssl_args = Vec::new();
            if let Some(pid_filter) = pid_target {
                ssl_args.extend(["-p".to_string(), pid_filter.to_string()]);
            }
            if let Some(uid_filter) = ssl_uid {
                ssl_args.extend(["-u".to_string(), uid_filter.to_string()]);
            }
            if !skip_ssl_comm_filter {
                if let Some(comm_filter) = comm {
                    ssl_args.extend(["-c".to_string(), comm_filter.to_string()]);
                }
            }
            if ssl_handshake {
                ssl_args.push("--handshake".to_string());
            }
            if let Some(path) = resolved_binary_path.as_deref() {
                ssl_args.extend(["--binary-path".to_string(), path.to_string()]);
            }
            if !ssl_args.is_empty() {
                ssl_runner = ssl_runner.with_args(&ssl_args);
            }

            ssl_runner = ssl_runner.add_analyzer(Box::new(TimestampNormalizer::new()));

            if !ssl_filter.is_empty() {
                ssl_runner = ssl_runner
                    .add_analyzer(Box::new(SSLFilter::with_patterns(ssl_filter.to_vec())));
            }

            if ssl_http {
                ssl_runner =
                    ssl_runner.add_analyzer(Box::new(SSEProcessor::new_with_timeout(30000)));

                let http_parser = if ssl_raw_data {
                    HTTPParser::new()
                } else {
                    HTTPParser::new().disable_raw_data()
                };
                ssl_runner = ssl_runner.add_analyzer(Box::new(http_parser));

                if !http_filter.is_empty() {
                    ssl_runner = ssl_runner
                        .add_analyzer(Box::new(HTTPFilter::with_patterns(http_filter.to_vec())));
                }

                if !disable_auth_removal {
                    ssl_runner = ssl_runner.add_analyzer(Box::new(AuthHeaderRemover::new()));
                }
            }

            agent = agent.add_runner(Box::new(ssl_runner));
        }
        let http_filter_info = if ssl_http && !http_filter.is_empty() {
            format!(" with {} HTTP filter patterns", http_filter.len())
        } else {
            String::new()
        };
        println!("✓ SSL monitoring enabled{}", http_filter_info);
    }

    // Add stdio runner if enabled
    if stdio_enabled {
        let pid_filter =
            pid.ok_or_else(|| RunnerError::from("stdio capture currently requires --pid"))?;
        let mut stdio_runner =
            StdioRunner::from_binary_extractor(binary_extractor.get_stdiocap_path()?);
        let stdio_args = build_stdio_args(
            pid_filter,
            stdio_uid,
            stdio_comm,
            stdio_all_fds,
            stdio_max_bytes,
        );

        stdio_runner = stdio_runner.with_args(&stdio_args);
        stdio_runner = stdio_runner.add_analyzer(Box::new(TimestampNormalizer::new()));

        agent = agent.add_runner(Box::new(stdio_runner));
        println!("✓ Stdio monitoring enabled for PID {}", pid_filter);
    }

    // Add process runner if enabled
    if process_enabled {
        let process_targets: Vec<Option<u32>> = if pid_targets.is_empty() {
            vec![pid]
        } else {
            pid_targets.iter().copied().map(Some).collect()
        };

        for pid_target in process_targets {
            let mut process_runner =
                ProcessRunner::from_binary_extractor(binary_extractor.get_process_path());
            let mut process_args = Vec::new();
            if let Some(comm_filter) = comm {
                process_args.extend(["-c".to_string(), comm_filter.to_string()]);
            }
            if let Some(pid_filter) = pid_target {
                process_args.extend(["-p".to_string(), pid_filter.to_string()]);
            }
            if let Some(duration_filter) = duration {
                process_args.extend(["-d".to_string(), duration_filter.to_string()]);
            }
            if let Some(mode_filter) = mode {
                process_args.extend(["-m".to_string(), mode_filter.to_string()]);
            }

            if !process_args.is_empty() {
                process_runner = process_runner.with_args(&process_args);
            }

            process_runner = process_runner.add_analyzer(Box::new(TimestampNormalizer::new()));
            agent = agent.add_runner(Box::new(process_runner));
        }
        println!("✓ Process monitoring enabled");
    }

    // Add system resource runner if enabled
    if system_enabled {
        let system_targets: Vec<Option<u32>> = if pid_targets.is_empty() {
            vec![pid]
        } else {
            pid_targets.iter().copied().map(Some).collect()
        };

        for pid_target in system_targets {
            let mut system_runner = SystemRunner::new().interval(system_interval);

            if let Some(comm_filter) = comm {
                system_runner = system_runner.comm(comm_filter);
            }

            if let Some(pid_filter) = pid_target {
                system_runner = system_runner.pid(pid_filter);
            }

            system_runner = system_runner.add_analyzer(Box::new(TimestampNormalizer::new()));
            agent = agent.add_runner(Box::new(system_runner));
        }
        println!(
            "✓ System monitoring enabled (interval: {}s)",
            system_interval
        );
    }

    // Ensure at least one runner is enabled
    if !ssl_enabled && !process_enabled && !stdio_enabled && !system_enabled {
        return Err(
            "At least one monitoring type must be enabled (--ssl, --process, --stdio, or --system)"
                .into(),
        );
    }

    // Add global analyzers (HTTP filter is now added to SSL runner instead)

    // Create ResponsibilityRuntime with persistence and alert broadcasting
    let responsibility_db_path = format!("{}.responsibility.db", log_file);
    let persistence_config = crate::responsibility::persistence::WriterConfig {
        db_path: responsibility_db_path.clone(),
        ..Default::default()
    };
    let persistence_writer = PersistenceWriter::new(persistence_config)
        .map_err(|e| format!("failed to create persistence writer: {e}"))?;
    let (alert_tx, _alert_rx) = broadcast::channel::<ResponsibilityAlert>(256);

    let mut runtime_config = RuntimeConfig {
        debounce_ms: 5000,
        trigger_every_n: 100,
        max_observer_calls: 50,
        classifier: None,
        disable_observer: disable_responsibility_observer,
    };
    // Observer LLM — portable multi-source config loading.
    // 1. Environment variables (works on ANY machine, no config file needed).
    // 2. Config file paths (relative to cwd).
    // 3. Mock provider (zero-token dev/testing fallback).
    let observer: Box<dyn crate::responsibility::observer::provider::ObserverProvider> =
        match crate::responsibility::observer::provider::ObserverLLMProvider::from_env() {
            Ok(provider) => {
                println!("✓ Observer: LLM provider loaded from environment variables");
                Box::new(provider)
            }
            Err(_) => {
                let config_paths = ["env/config/observer.toml", "observer_llm.toml"];
                let mut loaded = None;
                for path in &config_paths {
                    if let Ok(provider) = crate::responsibility::observer::provider::ObserverLLMProvider::from_config_file(path) {
                        println!("✓ Observer: LLM provider loaded from {path}");
                        loaded = Some(Box::new(provider)
                            as Box<dyn crate::responsibility::observer::provider::ObserverProvider>);
                        break;
                    }
                }
                loaded.unwrap_or_else(|| {
                    println!("! Observer: config not found, using Mock (zero-token dev mode)");
                    Box::new(crate::responsibility::observer::mock::MockObserverProvider::with_responses(vec![]))
                })
            }
        };
    // Load ONNX DeBERTa classifier for Content Boundary Layer 3.
    // Graceful degradation: if model.onnx is not found, Layer 3 is disabled
    // but all other detection layers continue to function.
    // Portable model path lookup: try common locations.
    // For open-source deployment, users can set MTAGENTLENS_MODEL_DIR.
    let classifier_model_dir = std::env::var("MTAGENTLENS_MODEL_DIR")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| {
            let candidates = [
                std::path::PathBuf::from("collector/models/deberta"),
                std::path::PathBuf::from("models/deberta"),
            ];
            candidates.into_iter().find(|p| p.join("model.onnx").exists())
                .unwrap_or_else(|| std::path::PathBuf::from("collector/models/deberta"))
        });
    let classifier = match crate::responsibility::judgment::classifier::OnnxClassifier::try_new(&classifier_model_dir) {
        Ok(Some(c)) => {
            println!("✓ DeBERTa classifier: Layer 3 prompt injection detection enabled");
            Some(std::sync::Arc::new(c))
        }
        Ok(None) => {
            println!("! DeBERTa classifier: model.onnx not found, Layer 3 disabled");
            None
        }
        Err(e) => {
            println!("! DeBERTa classifier: failed to start ({e}), Layer 3 disabled");
            None
        }
    };
    runtime_config.classifier = classifier;

    let runtime = ResponsibilityRuntime::new(
        runtime_config,
        persistence_writer,
        alert_tx,
        observer,
    );
    agent = agent.add_global_analyzer(Box::new(runtime));
    println!("✓ Responsibility runtime enabled (Phase 1 streaming + Phase 2 batch)");

    agent = agent.add_global_analyzer(Box::new(MultiAgentAnomalyAnalyzer::new()));
    println!("✓ Multi-agent anomaly detection enabled");

    agent = agent.add_global_analyzer(Box::new(HandoffInjectionAnalyzer::new()));
    println!("✓ Handoff injection detection enabled");

    // FAAP-001: FileAccessAuditAnalyzer — ENABLED.
    // Monitors file access patterns with workspace boundary awareness.
    // Detects suspicious access to system dirs and files outside workspace.
    agent = agent.add_global_analyzer(Box::new(FileAccessAuditAnalyzer::new(
        FileAccessAuditConfig::default(),
        vec![
            std::path::PathBuf::from("/home/lzk/MtAgentLens"),
            std::path::PathBuf::from("/tmp"),
        ],
    )));
    #[allow(clippy::print_stdout)]
    println!("✓ File access audit analyzer enabled");

    agent = agent.add_global_analyzer(Box::new(
        SecuritySignoffAnalyzer::new(SecuritySignoffConfig::default()),
    ));
    println!("✓ Security signoff tracking enabled");


    if disable_responsibility_observer {
        println!("! Responsibility Observer LLM disabled — using heuristic fallback (ablation A4 mode)");
    } else {
        println!("✓ Responsibility Observer LLM enabled");
    }

    agent = agent.add_global_analyzer(Box::new(if rotate_logs {
        FileLogger::with_max_size(log_file, max_log_size).unwrap()
    } else {
        FileLogger::new(log_file).unwrap()
    }));
    println!("✓ Logging to file: {}", log_file);

    if !quiet {
        agent = agent.add_global_analyzer(Box::new(OutputAnalyzer::new()));
        println!("✓ Console output enabled");
    }

    println!("{}", "=".repeat(60));
    println!(
        "Starting flexible trace monitoring with {} runners and {} global analyzers...",
        agent.runner_count(),
        agent.analyzer_count()
    );
    println!("Press Ctrl+C to stop");

    // Start web server if enabled
    let _server_handle = start_web_server_if_enabled(
        enable_server,
        server_port,
        Some(log_file),
        event_sender.clone(),
    )
    .await
    .map_err(|e| RunnerError::from(format!("Failed to start server: {}", e)))?;

    let mut stream = agent.run().await?;

    // Consume the stream to actually process events
    loop {
        tokio::select! {
            event = stream.next() => {
                match event {
                    Some(event) => {
                        if enable_server {
                            let _ = event_sender.send(event);
                        }
                    }
                    None => break,
                }
            }
            _ = shutdown_requested() => {
                println!("Trace agent shutting down gracefully...");
                break;
            }
        }
    }

    Ok(())
}

// Shared server management function
/// Monitor system resources (CPU and memory)
async fn run_system(
    interval: u64,
    pid: Option<u32>,
    comm: Option<&str>,
    include_children: bool,
    cpu_threshold: Option<f64>,
    memory_threshold: Option<u64>,
    log_file: &str,
    quiet: bool,
    rotate_logs: bool,
    max_log_size: u64,
    enable_server: bool,
    server_port: u16,
) -> Result<(), RunnerError> {
    println!("System Resource Monitoring");
    println!("{}", "=".repeat(60));

    let mut system_runner = SystemRunner::new().interval(interval);

    // Configure monitoring target
    if let Some(pid) = pid {
        system_runner = system_runner.pid(pid);
        println!("Monitoring PID: {}", pid);
    } else if let Some(comm) = comm {
        system_runner = system_runner.comm(comm);
        println!("Monitoring process: {}", comm);
    } else {
        println!("Monitoring system-wide resources");
    }

    // Configure options
    system_runner = system_runner.include_children(include_children);

    if let Some(threshold) = cpu_threshold {
        system_runner = system_runner.cpu_threshold(threshold);
        println!("CPU alert threshold: {}%", threshold);
    }

    if let Some(threshold) = memory_threshold {
        system_runner = system_runner.memory_threshold(threshold);
        println!("Memory alert threshold: {} MB", threshold);
    }

    println!("Interval: {}s", interval);
    println!("Include children: {}", include_children);
    println!("{}", "=".repeat(60));
    println!("Starting system monitoring (press Ctrl+C to stop):");

    // Set up event broadcasting for server if enabled
    let (event_sender, _event_receiver) = broadcast::channel(1000);

    // Add TimestampNormalizer first
    system_runner = system_runner.add_analyzer(Box::new(TimestampNormalizer::new()));

    // Add file logger
    system_runner = system_runner.add_analyzer(Box::new(if rotate_logs {
        FileLogger::with_max_size(log_file, max_log_size).unwrap()
    } else {
        FileLogger::new(log_file).unwrap()
    }));

    // Add console output unless quiet
    if !quiet {
        system_runner = system_runner.add_analyzer(Box::new(OutputAnalyzer::new()));
    }

    // Start web server if enabled
    let _server_handle = start_web_server_if_enabled(
        enable_server,
        server_port,
        Some(log_file),
        event_sender.clone(),
    )
    .await
    .map_err(|e| RunnerError::from(format!("Failed to start server: {}", e)))?;

    let mut stream = system_runner.run().await?;

    // Consume the stream to actually process events
    loop {
        tokio::select! {
            event = stream.next() => {
                match event {
                    Some(event) => {
                        if enable_server {
                            let _ = event_sender.send(event);
                        }
                    }
                    None => break,
                }
            }
            _ = shutdown_requested() => {
                println!("System runner shutting down gracefully...");
                break;
            }
        }
    }

    Ok(())
}

async fn start_web_server_if_enabled(
    enable_server: bool,
    port: u16,
    log_file: Option<&str>,
    event_sender: broadcast::Sender<crate::framework::core::Event>,
) -> Result<Option<tokio::task::JoinHandle<()>>, Box<dyn std::error::Error>> {
    if !enable_server {
        return Ok(None);
    }

    let addr = format!("0.0.0.0:{}", port)
        .parse()
        .map_err(|e| format!("Invalid server address: {}", e))?;

    let web_server = WebServer::new(event_sender, log_file)
        .map_err(|e| format!("Failed to create web server: {}", e))?;

    println!("🌐 Starting web server on http://{}", addr);
    println!("   Frontend will be available once the server starts");

    let server_handle = tokio::spawn(async move {
        if let Err(e) = web_server.start(addr).await {
            eprintln!("❌ Web server error: {}", e);
        }
    });

    // Give the server a moment to start
    tokio::time::sleep(tokio::time::Duration::from_millis(100)).await;

    Ok(Some(server_handle))
}
