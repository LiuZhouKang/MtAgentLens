// SPDX-License-Identifier: Apache-2.0 OR MIT
// Copyright (c) 2026 MtAgentLens Project

// 基于文件元数据的隐蔽信道 — 发送方/接收方
//
// 协议: comm-protocol.md
// 编码: 每个字节映射到文件权限位的低 8 位
// 验证: mtime 纳秒字段提供冗余校验

use anyhow::{Context, bail};
use clap::{Parser, Subcommand};
use std::fs;
use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

// ---------------------------------------------------------------------------
// 协议常量
// ---------------------------------------------------------------------------

/// 普通文件 + owner read (S_IFREG | 0o400)
/// 低 8 位全零，确保 byte_value 编码不会被权限基底位污染
const BASE_MODE: u32 = 0o100400;

/// 编码一个字节到文件权限位: mode = BASE_MODE | byte_value
fn encode_byte(byte: u8) -> u32 {
    BASE_MODE | (byte as u32)
}

/// 从文件权限位解码字节: byte = mode & 0xFF
fn decode_byte(mode: u32) -> u8 {
    (mode & 0xFF) as u8
}

/// 从 mtime 纳秒解码字节（冗余校验通道）
fn decode_byte_from_nsec(mtime_nsec: u32) -> u8 {
    (mtime_nsec % 256) as u8
}

/// 设置文件的 mtime，使纳秒部分的低 8 位等于给定字节
fn set_mtime_with_byte(path: &Path, byte: u8) -> anyhow::Result<()> {
    // 使用当前时间，但修改纳秒部分
    let now = SystemTime::now();
    let dur = now.duration_since(UNIX_EPOCH)?;
    let secs = dur.as_secs();
    let target_nsec = (dur.subsec_nanos() & !0xFF) | (byte as u32);
    // note: 纯 Rust 无法设置任意纳秒 — 我们使用 filetime crate 或直接 shell out
    // 这里我们依赖权限位作为主通道，mtime 在支持的系统上作为冗余
    // 在 Linux 上通过 /usr/bin/touch 或 libc 设置
    let t = filetime::FileTime::from_unix_time(secs as i64, target_nsec);
    filetime::set_file_mtime(path, t)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// 发送方
// ---------------------------------------------------------------------------

fn send_message(channel_dir: &Path, message: &[u8]) -> anyhow::Result<()> {
    // 确保目录干净
    if channel_dir.exists() {
        fs::remove_dir_all(channel_dir).context("清理旧目录失败")?;
    }
    fs::create_dir_all(channel_dir).context("创建信道目录失败")?;

    let msg_len = message.len();

    // 1. 创建 control.start
    let start_path = channel_dir.join("control.start");
    fs::File::create(&start_path).context("创建 control.start 失败")?;
    let mut start_perm = fs::metadata(&start_path)?.permissions();
    start_perm.set_mode(BASE_MODE);
    fs::set_permissions(&start_path, start_perm).context("设置 control.start 权限失败")?;

    // 2. 逐字节创建 msg_NNNN
    for (i, &byte) in message.iter().enumerate() {
        let msg_path = channel_dir.join(format!("msg_{:04}", i));
        let mut f =
            fs::File::create(&msg_path).with_context(|| format!("创建 msg_{:04} 失败", i))?;
        // 写入字节本身作为文件内容（方便调试）
        f.write_all(&[byte])?;
        drop(f);

        // 设置权限位编码
        let mut perm = fs::metadata(&msg_path)
            .with_context(|| format!("获取 msg_{:04} 元数据失败", i))?
            .permissions();
        perm.set_mode(encode_byte(byte));
        fs::set_permissions(&msg_path, perm)
            .with_context(|| format!("设置 msg_{:04} 权限失败", i))?;

        // 设置 mtime 冗余编码
        let _ = set_mtime_with_byte(&msg_path, byte);
    }

    // 3. 创建 control.end，文件大小 = 消息长度
    let end_path = channel_dir.join("control.end");
    {
        let mut f = fs::File::create(&end_path).context("创建 control.end 失败")?;
        // 写入占位内容使文件大小等于消息长度
        let padding = vec![0u8; msg_len];
        f.write_all(&padding)?;
    }
    let mut end_perm = fs::metadata(&end_path)?.permissions();
    end_perm.set_mode(BASE_MODE);
    fs::set_permissions(&end_path, end_perm)?;

    println!(
        "[sender] 发送完成: {} 个字节 → {}",
        msg_len,
        channel_dir.display()
    );
    println!("[sender] 文件列表:");
    for entry in fs::read_dir(channel_dir)? {
        let e = entry?;
        let meta = e.metadata()?;
        println!(
            "  {:30}  mode={:06o}  size={}",
            e.file_name().to_string_lossy(),
            meta.permissions().mode(),
            meta.len()
        );
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// 接收方
// ---------------------------------------------------------------------------

fn receive_message(
    channel_dir: &Path,
    poll_interval_ms: u64,
    timeout_secs: u64,
) -> anyhow::Result<Vec<u8>> {
    let start_path = channel_dir.join("control.start");
    let end_path = channel_dir.join("control.end");

    // 等待 control.start
    let start_deadline = std::time::Instant::now() + Duration::from_secs(timeout_secs);
    loop {
        if start_path.exists() {
            break;
        }
        if std::time::Instant::now() > start_deadline {
            bail!("超时: 等待 control.start 超过 {} 秒", timeout_secs);
        }
        std::thread::sleep(Duration::from_millis(poll_interval_ms));
    }
    println!("[receiver] 检测到 control.start，开始接收...");

    // 检测现有消息文件并轮询等待 control.end
    let mut received: Vec<u8> = Vec::new();
    let mut next_seq: usize = 0;
    let end_deadline = std::time::Instant::now() + Duration::from_secs(timeout_secs);

    loop {
        // 读取所有当前可用的消息文件（按序列号）
        loop {
            let msg_path = channel_dir.join(format!("msg_{:04}", next_seq));
            if !msg_path.exists() {
                break; // 等待下一个
            }

            let meta = fs::metadata(&msg_path)
                .with_context(|| format!("获取 msg_{:04} 元数据失败", next_seq))?;
            let mode = meta.permissions().mode();

            // 主通道: 权限位解码
            let byte_from_perm = decode_byte(mode);

            // 冗余通道: mtime 纳秒
            let byte_from_mtime = meta.modified().ok().and_then(|t| {
                t.duration_since(UNIX_EPOCH)
                    .ok()
                    .map(|d| decode_byte_from_nsec(d.subsec_nanos()))
            });

            // 交叉验证（如果冗余通道可用）
            if let Some(mt_byte) = byte_from_mtime
                && mt_byte != byte_from_perm
            {
                println!(
                    "[receiver] 警告: msg_{:04} 权限位={:#04x} mtime={:#04x} 不匹配，使用权限位",
                    next_seq, byte_from_perm, mt_byte
                );
            }

            received.push(byte_from_perm);
            next_seq += 1;
        }

        // 检查 control.end 是否就绪
        if end_path.exists() {
            let end_meta = fs::metadata(&end_path)?;
            let expected_len = end_meta.len() as usize;

            println!(
                "[receiver] 检测到 control.end，期望长度={}, 实际接收={}",
                expected_len,
                received.len()
            );

            if received.len() != expected_len {
                bail!(
                    "长度不匹配: control.end 声明 {} 字节，实际解码 {} 字节",
                    expected_len,
                    received.len()
                );
            }

            // 验证序列号连续性
            for seq in 0..received.len() {
                let p = channel_dir.join(format!("msg_{:04}", seq));
                if !p.exists() {
                    bail!("消息文件缺失: msg_{:04}", seq);
                }
            }

            break;
        }

        if std::time::Instant::now() > end_deadline {
            bail!(
                "超时: 等待 control.end 超过 {} 秒（已接收 {} 字节）",
                timeout_secs,
                received.len()
            );
        }
        std::thread::sleep(Duration::from_millis(poll_interval_ms));
    }

    println!("[receiver] 接收完成: {} 字节", received.len());
    Ok(received)
}

// ---------------------------------------------------------------------------
// CLI
// ---------------------------------------------------------------------------

#[derive(Parser)]
#[command(name = "covert-channel", about = "基于文件元数据的隐蔽信道测试工具")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// 发送消息
    Send {
        /// 信道目录路径
        #[arg(short, long, default_value = "/tmp/covert-test")]
        dir: PathBuf,

        /// 要发送的消息
        #[arg(short, long)]
        message: String,
    },
    /// 接收消息
    Receive {
        /// 信道目录路径
        #[arg(short, long, default_value = "/tmp/covert-test")]
        dir: PathBuf,

        /// 轮询间隔（毫秒）
        #[arg(long, default_value = "100")]
        poll_ms: u64,

        /// 超时时间（秒）
        #[arg(long, default_value = "30")]
        timeout: u64,
    },
    /// 运行往返测试（单进程自测）
    Test {
        /// 信道目录路径
        #[arg(short, long, default_value = "/tmp/covert-test")]
        dir: PathBuf,

        /// 测试消息
        #[arg(short, long, default_value = "Hello, covert channel! 你好，隐蔽信道！")]
        message: String,
    },
}

fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Command::Send { dir, message } => {
            send_message(&dir, message.as_bytes())?;
        }
        Command::Receive {
            dir,
            poll_ms,
            timeout,
        } => {
            let result = receive_message(&dir, poll_ms, timeout)?;
            let text = String::from_utf8_lossy(&result);
            println!("[receiver] 解码消息: {}", text);
        }
        Command::Test { dir, message } => {
            println!("=== 隐蔽信道往返测试 ===");
            println!("信道目录: {}", dir.display());
            println!("原始消息 ({} 字节): {:?}", message.len(), message);

            // 清理旧目录
            if dir.exists() {
                fs::remove_dir_all(&dir)?;
            }

            // 发送
            send_message(&dir, message.as_bytes())?;

            // 接收（发送已完成，control.start 和 control.end 都已就绪）
            let decoded = receive_message(&dir, 10, 5)?;
            let decoded_text = String::from_utf8_lossy(&decoded);

            println!();
            if decoded.as_slice() == message.as_bytes() {
                println!("✅ 往返测试通过！");
                println!("   原始: {:?}", message);
                println!("   解码: {:?}", decoded_text);
            } else {
                println!("❌ 往返测试失败！");
                println!("   原始 ({} 字节): {:?}", message.len(), message.as_bytes());
                println!("   解码 ({} 字节): {:?}", decoded.len(), decoded.as_slice());
                // 逐字节比较
                let max_len = message.len().max(decoded.len());
                for i in 0..max_len {
                    let orig = message.as_bytes().get(i).copied();
                    let dec = decoded.get(i).copied();
                    if orig != dec {
                        println!("   差异位置 {}: 原始={:02x?} 解码={:02x?}", i, orig, dec);
                    }
                }
                std::process::exit(1);
            }
        }
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// 测试
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_encode_decode_byte_all_values() {
        for byte in 0..=255u8 {
            let mode = encode_byte(byte);
            assert!(mode & 0o170000 == 0o100000, "必须是普通文件");
            assert_eq!(decode_byte(mode), byte, "编码解码往返失败: {:#04x}", byte);
        }
    }

    #[test]
    fn test_encode_decode_boundaries() {
        // 边界值
        assert_eq!(decode_byte(encode_byte(0x00)), 0x00);
        assert_eq!(decode_byte(encode_byte(0xFF)), 0xFF);
        assert_eq!(decode_byte(encode_byte(0x41)), b'A');
        assert_eq!(decode_byte(encode_byte(0x0A)), b'\n');
    }

    #[test]
    fn test_decode_byte_from_nsec() {
        assert_eq!(decode_byte_from_nsec(0), 0);
        assert_eq!(decode_byte_from_nsec(255), 255);
        assert_eq!(decode_byte_from_nsec(256), 0); // wraps
        assert_eq!(decode_byte_from_nsec(511), 255);
    }

    #[test]
    fn test_send_receive_empty_message() {
        let dir = tempfile::TempDir::new().unwrap();
        let channel = dir.path().join("channel");
        let msg = b"";
        send_message(&channel, msg).unwrap();
        let decoded = receive_message(&channel, 10, 5).unwrap();
        assert_eq!(decoded, b"");
    }

    #[test]
    fn test_send_receive_simple_message() {
        let dir = tempfile::TempDir::new().unwrap();
        let channel = dir.path().join("channel");
        let msg = b"Hello, World!";
        send_message(&channel, msg).unwrap();
        let decoded = receive_message(&channel, 10, 5).unwrap();
        assert_eq!(decoded.as_slice(), msg);
    }

    #[test]
    fn test_send_receive_binary_message() {
        let dir = tempfile::TempDir::new().unwrap();
        let channel = dir.path().join("channel");
        let msg: Vec<u8> = (0..=255).collect(); // 所有字节值
        send_message(&channel, &msg).unwrap();
        let decoded = receive_message(&channel, 10, 5).unwrap();
        assert_eq!(decoded, msg);
    }

    #[test]
    fn test_send_receive_unicode_message() {
        let dir = tempfile::TempDir::new().unwrap();
        let channel = dir.path().join("channel");
        let msg = "你好，隐蔽信道！🕵️‍♂️ covert channel 测试";
        send_message(&channel, msg.as_bytes()).unwrap();
        let decoded = receive_message(&channel, 10, 5).unwrap();
        assert_eq!(decoded.as_slice(), msg.as_bytes());
    }

    #[test]
    fn test_send_receive_long_message() {
        let dir = tempfile::TempDir::new().unwrap();
        let channel = dir.path().join("channel");
        // 长消息：10KB 的重复模式
        let msg: Vec<u8> = (0..10_000).map(|i| (i % 256) as u8).collect();
        send_message(&channel, &msg).unwrap();
        let decoded = receive_message(&channel, 10, 10).unwrap();
        assert_eq!(decoded.len(), msg.len());
        assert_eq!(decoded, msg);
    }

    #[test]
    fn test_send_receive_control_files_exist() {
        let dir = tempfile::TempDir::new().unwrap();
        let channel = dir.path().join("channel");
        send_message(&channel, b"test").unwrap();

        assert!(channel.join("control.start").exists());
        assert!(channel.join("control.end").exists());
        assert!(channel.join("msg_0000").exists());
        assert!(channel.join("msg_0003").exists()); // "test" = 4 bytes, indices 0-3

        let end_meta = fs::metadata(channel.join("control.end")).unwrap();
        assert_eq!(end_meta.len(), 4); // 文件大小 = 消息长度
    }

    #[test]
    fn test_receive_timeout_no_start() {
        let dir = tempfile::TempDir::new().unwrap();
        let channel = dir.path().join("channel");
        fs::create_dir_all(&channel).unwrap();
        // 不创建 control.start — 应该超时
        let result = receive_message(&channel, 10, 1);
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("超时"));
    }
}
