// SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause)
// Copyright (c) 2023 Yusheng Zheng
//
// Based on tls_capture from BCC by Adrian Lopez & Mark Drayton.
// 15-Aug-2023   Yusheng Zheng   Created this.
#ifndef __TLS_CAPTURE_H
#define __TLS_CAPTURE_H

// A single SSL_read/SSL_write call is bounded by the TLS record/application
// buffer, not by the whole HTTP body: SSE chunks and request writes are small.
// 256 KiB reservation gave the 64 MiB ring only ~256 slots and dropped 10.5k
// events during a real 140 s agent run (median payload 68 bytes, p99.9 ~16 KiB).
// 64 KiB cut drops to 4.8k but still wasted 1000x on median events; 32 KiB keeps
// 2x headroom over observed payloads and gives ~2048 slots.
#define MAX_BUF_SIZE (32 * 1024)
// Ringbuf reserve must use a compile-time constant size (the verifier rejects
// a computed size with "R2 is not a known constant"). Each event therefore
// consumes sizeof(probe_SSL_data_t) ≈ 32 KiB.
#define RING_BUFFER_SIZE (64 * 1024 * 1024)
#define TASK_COMM_LEN 16

/* Loss reasons in the dropped_events array map (shared with userspace). */
enum tls_drop_reason {
    TLS_DROP_RINGBUF_FULL = 0,     /* bpf_ringbuf_reserve returned NULL */
    TLS_DROP_STATE_MAP_FAILED = 1, /* per-tid arg map update/lookup failed */
    TLS_DROP_REASON_MAX = 2,
};

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
    __u64 ssl_ptr;          // SSL* connection object; stable within one TLS session
    int is_handshake;
    // Must stay last: the BPF program reserves only the header plus the bytes
    // it actually copies. Reserving the full 256 KiB struct per event gave the
    // 8 MiB ring exactly ~31 slots, so ordinary traffic dropped events and
    // split HTTP bodies (which then failed to parse).
    __u8 buf[MAX_BUF_SIZE];
};

#endif /* __TLS_CAPTURE_H */
