// SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause)
// Copyright (c) 2023 Yusheng Zheng
//
// Based on tls_capture from BCC by Adrian Lopez & Mark Drayton.
// 15-Aug-2023   Yusheng Zheng   Created this.
#ifndef __TLS_CAPTURE_H
#define __TLS_CAPTURE_H

#define MAX_BUF_SIZE (256 * 1024)  // 256KB — captures full API request bodies including system prompts
#define RING_BUFFER_SIZE (8 * 1024 * 1024)  // 8MB ring buffer with variable-length entries
#define TASK_COMM_LEN 16

struct probe_SSL_data_t {
    __u64 timestamp_ns;
    __u64 delta_ns;
    __u32 pid;
    __u32 tid;
    __u32 uid;
    __u32 len;
    __u32 buf_size;         // Actual bytes copied to buf
    int buf_filled;
    int rw;
    char comm[TASK_COMM_LEN];
    __u8 buf[MAX_BUF_SIZE];
    __u64 ssl_ptr;          // SSL* connection object; stable within one TLS session
    int is_handshake;
};

#endif /* __TLS_CAPTURE_H */
