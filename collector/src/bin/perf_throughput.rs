// SPDX-License-Identifier: MIT
// perf_throughput.rs — Collector 管道吞吐量基准测试
//
// 使用 FakeRunner 生成 N 个合成 SSL 事件, 以 0ms 延迟注入管道,
// 测量全链路吞吐量 (events/sec)。
//
// 用法: cargo run --release --bin perf_throughput -- [events=100000]

use mtagentprism::framework::analyzers::{HTTPParser, OutputAnalyzer};
use mtagentprism::framework::runners::{FakeRunner, Runner};
use futures::stream::StreamExt;
use std::env;
use std::time::Instant;

#[tokio::main]
async fn main() {
    let args: Vec<String> = env::args().collect();
    let event_count: usize = args.get(1).and_then(|s| s.parse().ok()).unwrap_or(100_000);
    let delay_ms: u64 = 0;

    eprintln!("=== AgentSight Collector Throughput Benchmark ===");
    eprintln!("Events: {} ({} pairs)", event_count * 2, event_count);
    eprintln!("Delay between pairs: {}ms", delay_ms);

    // ── Full Pipeline ────────────────────────────────────────────────
    eprintln!("\n--- Full Pipeline (HTTPParser + OutputAnalyzer) ---");

    let mut runner = FakeRunner::new()
        .event_count(event_count)
        .delay_ms(delay_ms)
        .add_analyzer(Box::new(HTTPParser::new()))
        .add_analyzer(Box::new(OutputAnalyzer::new()));

    let start = Instant::now();
    let stream = runner.run().await.expect("FakeRunner failed");
    let events: Vec<_> = stream.collect().await;
    let elapsed_full = start.elapsed();
    let eps_full = events.len() as f64 / elapsed_full.as_secs_f64();

    eprintln!("  Total events: {}", events.len());
    eprintln!("  Wall time: {:.2?}", elapsed_full);
    eprintln!("  Throughput: {:.0} events/sec", eps_full);

    let ssl_count = events.iter().filter(|e| e.source == "ssl").count();
    let http_count = events.iter().filter(|e| e.source == "http").count();
    eprintln!("  SSL events: {}, HTTP events: {}", ssl_count, http_count);

    // ── Passthrough (No Analyzers) ──────────────────────────────────
    eprintln!("\n--- Passthrough (No Analyzers) ---");

    let mut runner_raw = FakeRunner::new()
        .event_count(event_count)
        .delay_ms(delay_ms);

    let start_raw = Instant::now();
    let stream_raw = runner_raw.run().await.expect("FakeRunner failed");
    let events_raw: Vec<_> = stream_raw.collect().await;
    let elapsed_raw = start_raw.elapsed();
    let eps_raw = events_raw.len() as f64 / elapsed_raw.as_secs_f64();

    eprintln!("  Total events: {}", events_raw.len());
    eprintln!("  Wall time: {:.2?}", elapsed_raw);
    eprintln!("  Throughput: {:.0} events/sec", eps_raw);

    // ── Summary ─────────────────────────────────────────────────────
    let overhead_pct = if elapsed_raw.as_secs_f64() > 0.0 {
        (elapsed_full.as_secs_f64() - elapsed_raw.as_secs_f64())
            / elapsed_raw.as_secs_f64() * 100.0
    } else {
        0.0
    };

    eprintln!("\n=== Results ===");
    eprintln!("Full Pipeline:     {:>10.0} events/sec", eps_full);
    eprintln!("Passthrough:       {:>10.0} events/sec", eps_raw);
    eprintln!("Analyzer Overhead: {:>8.1}%", overhead_pct);

    // JSON output for parsing
    println!(
        "{}",
        serde_json::json!({
            "event_count": event_count,
            "total_events": events.len(),
            "raw_events": events_raw.len(),
            "full_pipeline": {
                "elapsed_s": elapsed_full.as_secs_f64(),
                "events_per_sec": eps_full,
                "ssl_events": ssl_count,
                "http_events": http_count,
            },
            "passthrough": {
                "elapsed_s": elapsed_raw.as_secs_f64(),
                "events_per_sec": eps_raw,
            },
            "analyzer_overhead_pct": overhead_pct,
        })
    );
}
