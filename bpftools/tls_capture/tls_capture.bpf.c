// SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause)
// Copyright (c) 2023 Yusheng Zheng
//
// Based on tls_capture from BCC by Adrian Lopez & Mark Drayton.
// 15-Aug-2023   Yusheng Zheng   Created this.
#include <vmlinux.h>
#include <bpf/bpf_core_read.h>
#include <bpf/bpf_endian.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include "tls_capture.h"

struct {
    __uint(type, BPF_MAP_TYPE_RINGBUF);
    __uint(max_entries, RING_BUFFER_SIZE);
} rb SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 10240);
    __type(key, __u32);
    __type(value, size_t*);
} readbytes_ptrs SEC(".maps");

#define MAX_ENTRIES 10240

#define min(x, y)                      \
    ({                                 \
        typeof(x) _min1 = (x);         \
        typeof(y) _min2 = (y);         \
        (void)(&_min1 == &_min2);      \
        _min1 < _min2 ? _min1 : _min2; \
    })

/* ssl_data per-CPU array removed - ring buffer allocates memory directly */

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, MAX_ENTRIES);
    __type(key, __u32);
    __type(value, __u64);
} start_ns SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, MAX_ENTRIES);
    __type(key, __u32);
    __type(value, __u64);
} bufs SEC(".maps");

/* tid -> SSL* connection object. Stashed on entry, read back on return.
 * Needed because a single tid (Node's event loop) multiplexes several
 * concurrent TLS connections, so tid alone cannot identify a connection. */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, MAX_ENTRIES);
    __type(key, __u32);
    __type(value, __u64);
} ssls SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, TLS_DROP_REASON_MAX);
    __type(key, __u32);
    __type(value, __u64);
} dropped_events SEC(".maps");

const volatile pid_t targ_pid = 0;
const volatile uid_t targ_uid = -1;

static __always_inline bool trace_allowed(u32 uid, u32 pid)
{
    /* filters */
    if (targ_pid && targ_pid != pid)
        return false;
    if (targ_uid != -1) {
        if (targ_uid != uid) {
            return false;
        }
    }
    return true;
}

static __always_inline void inc_dropped(enum tls_drop_reason reason) {
    __u32 key = (__u32)reason;
    __u64 *cnt = bpf_map_lookup_elem(&dropped_events, &key);
    if (cnt)
        __sync_fetch_and_add(cnt, 1);
}

/* Record per-tid call arguments. A failed update is a lost event, so it is
 * counted. Nested wrapper calls (SSL_read -> SSL_read_ex) re-enter the same
 * tid by design; the inner call captures the data, and a genuine unpaired
 * outer call is caught at exit as a lookup miss. Counting every re-entry
 * would mark healthy OpenSSL traffic as lossy. */
static __always_inline int store_call_args(u32 tid, void *buf, u64 ts, void *ssl) {
    if (bpf_map_update_elem(&bufs, &tid, &buf, BPF_ANY) ||
        bpf_map_update_elem(&start_ns, &tid, &ts, BPF_ANY) ||
        bpf_map_update_elem(&ssls, &tid, &ssl, BPF_ANY)) {
        inc_dropped(TLS_DROP_STATE_MAP_FAILED);
        bpf_map_delete_elem(&bufs, &tid);
        bpf_map_delete_elem(&start_ns, &tid);
        bpf_map_delete_elem(&ssls, &tid);
        return -1;
    }
    return 0;
}

SEC("uprobe/do_handshake")
int BPF_UPROBE(probe_SSL_rw_enter, void *ssl, void *buf, int num) {
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 pid = pid_tgid >> 32;
    u32 tid = pid_tgid;
    u32 uid = bpf_get_current_uid_gid();
    u64 ts = bpf_ktime_get_ns();

    if (!trace_allowed(uid, pid)) {
        return 0;
    }

    /* store arg info for later lookup */
    store_call_args(tid, buf, ts, ssl);
    return 0;
}

static int SSL_exit(struct pt_regs *ctx, int rw) {
    int ret = 0;
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 pid = pid_tgid >> 32;
    u32 tid = (u32)pid_tgid;
    u32 uid = bpf_get_current_uid_gid();
    u64 ts = bpf_ktime_get_ns();

    if (!trace_allowed(uid, pid)) {
        return 0;
    }

    /* store arg info for later lookup */
    u64 *bufp = bpf_map_lookup_elem(&bufs, &tid);
    if (bufp == 0) {
        /* The enter probe's bookkeeping is missing (map full, evicted, or
         * overwritten by a re-entrant call): this event is lost, count it. */
        inc_dropped(TLS_DROP_STATE_MAP_FAILED);
        return 0;
    }

    u64 *tsp = bpf_map_lookup_elem(&start_ns, &tid);
    if (!tsp) {
        inc_dropped(TLS_DROP_STATE_MAP_FAILED);
        return 0;
    }
    u64 delta_ns = ts - *tsp;

    /* Connection identity: stable across a TLS session, distinct across
     * concurrent sessions that share this tid (Node multiplexes sockets). */
    u64 *sslp = bpf_map_lookup_elem(&ssls, &tid);

    int len = PT_REGS_RC(ctx);
    if (len <= 0)  // no data
        return 0;

    /* reserve space in ring buffer */
    u32 buf_copy_size = min((size_t)MAX_BUF_SIZE, (size_t)len);
    /* Ringbuf reserve requires a compile-time constant size on this kernel
     * ("R2 is not a known constant"). A variable reserve (header + copied
     * bytes) was rejected by the verifier, which silently disabled TLS
     * capture. The ring is sized for the full record instead: 64 MiB gives
     * ~256 in-flight events at 256 KiB each. */
    struct probe_SSL_data_t *data = bpf_ringbuf_reserve(&rb, sizeof(*data), 0);
    if (!data) {
        inc_dropped(TLS_DROP_RINGBUF_FULL);
        return 0;
    }

    data->timestamp_ns = ts;
    data->delta_ns = delta_ns;
    data->pid = pid;
    data->tid = tid;
    data->uid = uid;
    data->len = (u32)len;
    data->buf_filled = 0;
    data->buf_size = 0;
    data->rw = rw;
    data->is_handshake = false;
    data->ssl_ptr = sslp ? *sslp : 0;

    bpf_get_current_comm(&data->comm, sizeof(data->comm));

    if (bufp != 0)
        ret = bpf_probe_read_user(&data->buf, buf_copy_size, (char *)*bufp);

    bpf_map_delete_elem(&bufs, &tid);
    bpf_map_delete_elem(&start_ns, &tid);
    bpf_map_delete_elem(&ssls, &tid);

    if (!ret) {
        data->buf_filled = 1;
        data->buf_size = buf_copy_size;
    } else {
        data->buf_filled = 0;
        data->buf_size = 0;
    }

    /* submit to ring buffer */
    bpf_ringbuf_submit(data, 0);
    return 0;
}

SEC("uretprobe/SSL_read")
int BPF_URETPROBE(probe_SSL_read_exit) {
    return (SSL_exit(ctx, 0));
}

SEC("uretprobe/SSL_write")
int BPF_URETPROBE(probe_SSL_write_exit) {
    return (SSL_exit(ctx, 1));
}

SEC("uprobe/SSL_write_ex")
int BPF_UPROBE(probe_SSL_write_ex_enter, void *ssl, void *buf, size_t num, size_t *readbytes) {
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 pid = pid_tgid >> 32;
    u32 tid = (u32)pid_tgid;
    u32 uid = bpf_get_current_uid_gid();
    u64 ts = bpf_ktime_get_ns();

    if (!trace_allowed(uid, pid)) {
        return 0;
    }

    if (store_call_args(tid, buf, ts, ssl))
        return 0;
    if (bpf_map_update_elem(&readbytes_ptrs, &tid, &readbytes, BPF_ANY))
        inc_dropped(TLS_DROP_STATE_MAP_FAILED);

    return 0;
}

SEC("uprobe/SSL_read_ex")
int BPF_UPROBE(probe_SSL_read_ex_enter, void *ssl, void *buf, size_t num, size_t *readbytes) {
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 pid = pid_tgid >> 32;
    u32 tid = (u32)pid_tgid;
    u32 uid = bpf_get_current_uid_gid();
    u64 ts = bpf_ktime_get_ns();

    if (!trace_allowed(uid, pid)) {
        return 0;
    }

    if (store_call_args(tid, buf, ts, ssl))
        return 0;
    if (bpf_map_update_elem(&readbytes_ptrs, &tid, &readbytes, BPF_ANY))
        inc_dropped(TLS_DROP_STATE_MAP_FAILED);

    return 0;
}

static int ex_SSL_exit(struct pt_regs *ctx, int rw, int len) {
    int ret = 0;
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 pid = pid_tgid >> 32;
    u32 tid = (u32)pid_tgid;
    u32 uid = bpf_get_current_uid_gid();
    u64 ts = bpf_ktime_get_ns();

    if (!trace_allowed(uid, pid)) {
        return 0;
    }

    /* store arg info for later lookup */
    u64 *bufp = bpf_map_lookup_elem(&bufs, &tid);
    if (bufp == 0) {
        /* The enter probe's bookkeeping is missing (map full, evicted, or
         * overwritten by a re-entrant call): this event is lost, count it. */
        inc_dropped(TLS_DROP_STATE_MAP_FAILED);
        return 0;
    }

    u64 *tsp = bpf_map_lookup_elem(&start_ns, &tid);
    if (!tsp) {
        inc_dropped(TLS_DROP_STATE_MAP_FAILED);
        return 0;
    }
    u64 delta_ns = ts - *tsp;

    /* Connection identity for the _ex variants (same tid multiplexing). */
    u64 *sslp = bpf_map_lookup_elem(&ssls, &tid);

    if (len <= 0)  // no data
        return 0;

    /* Explicit bounds clamping to satisfy eBPF verifier */
    u32 buf_copy_size = (u32)len & 0xFFFFF;
    if (buf_copy_size > MAX_BUF_SIZE)
        buf_copy_size = MAX_BUF_SIZE;

    /* reserve space in ring buffer */
    /* Ringbuf reserve requires a compile-time constant size on this kernel
     * ("R2 is not a known constant"). A variable reserve (header + copied
     * bytes) was rejected by the verifier, which silently disabled TLS
     * capture. The ring is sized for the full record instead: 64 MiB gives
     * ~256 in-flight events at 256 KiB each. */
    struct probe_SSL_data_t *data = bpf_ringbuf_reserve(&rb, sizeof(*data), 0);
    if (!data) {
        inc_dropped(TLS_DROP_RINGBUF_FULL);
        return 0;
    }

    data->timestamp_ns = ts;
    data->delta_ns = delta_ns;
    data->pid = pid;
    data->tid = tid;
    data->uid = uid;
    data->len = (u32)len;
    data->buf_filled = 0;
    data->buf_size = 0;
    data->rw = rw;
    data->is_handshake = false;
    data->ssl_ptr = sslp ? *sslp : 0;

    bpf_get_current_comm(&data->comm, sizeof(data->comm));

    if (bufp != 0)
        ret = bpf_probe_read_user(&data->buf, buf_copy_size, (char *)*bufp);

    bpf_map_delete_elem(&bufs, &tid);
    bpf_map_delete_elem(&start_ns, &tid);
    bpf_map_delete_elem(&ssls, &tid);

    if (!ret) {
        data->buf_filled = 1;
        data->buf_size = buf_copy_size;
    } else {
        data->buf_filled = 0;
        data->buf_size = 0;
    }

    /* submit to ring buffer */
    bpf_ringbuf_submit(data, 0);
    
    return 0;
}

SEC("uretprobe/SSL_write_ex")
int BPF_URETPROBE(probe_SSL_write_ex_exit)
{
    u32 tid = (u32)bpf_get_current_pid_tgid();
    size_t **readbytes_ptr = bpf_map_lookup_elem(&readbytes_ptrs, &tid);
    if (!readbytes_ptr)
        return 0;

    size_t written = 0;
    bpf_probe_read_user(&written, sizeof(written), *readbytes_ptr);
    bpf_map_delete_elem(&readbytes_ptrs, &tid);

    int ret = PT_REGS_RC(ctx);
    int len = (ret == 1) ? written : 0;

    return ex_SSL_exit(ctx, 1, len);
}

SEC("uretprobe/SSL_read_ex")
int BPF_URETPROBE(probe_SSL_read_ex_exit)
{
    u32 tid = (u32)bpf_get_current_pid_tgid();
    size_t **readbytes_ptr = bpf_map_lookup_elem(&readbytes_ptrs, &tid);
    if (!readbytes_ptr)
        return 0;

    size_t written = 0;
    bpf_probe_read_user(&written, sizeof(written), *readbytes_ptr);
    bpf_map_delete_elem(&readbytes_ptrs, &tid);

    int ret = PT_REGS_RC(ctx);
    int len = (ret == 1) ? written : 0;

    return ex_SSL_exit(ctx, 0, len);
}

SEC("uprobe/do_handshake")
int BPF_UPROBE(probe_SSL_do_handshake_enter, void *ssl) {
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 pid = pid_tgid >> 32;
    u32 tid = (u32)pid_tgid;
    u64 ts = bpf_ktime_get_ns();
    u32 uid = bpf_get_current_uid_gid();

    if (!trace_allowed(uid, pid)) {
        return 0;
    }

    /* store arg info for later lookup */
    bpf_map_update_elem(&start_ns, &tid, &ts, BPF_ANY);
    bpf_map_update_elem(&ssls, &tid, &ssl, BPF_ANY);
    return 0;
}

SEC("uretprobe/do_handshake")
int BPF_URETPROBE(probe_SSL_do_handshake_exit) {
    u64 pid_tgid = bpf_get_current_pid_tgid();
    u32 pid = pid_tgid >> 32;
    u32 tid = (u32)pid_tgid;
    u32 uid = bpf_get_current_uid_gid();
    u64 ts = bpf_ktime_get_ns();
    int ret = 0;

    /* trace_allowed(uid, pid) — pass the UID read above, not the tgid.
     * Passing tgid made the UID filter compare targ_uid against a process id,
     * so every handshake was dropped with no warning and no dropped-event
     * counter increment whenever a uid filter was configured. */
    if (!trace_allowed(uid, pid)) {
        return 0;
    }

    u64 *tsp = bpf_map_lookup_elem(&start_ns, &tid);
    if (tsp == 0)
        return 0;

    /* Connection identity (handshake events share the same ssls stash). */
    u64 *sslp = bpf_map_lookup_elem(&ssls, &tid);

    ret = PT_REGS_RC(ctx);
    if (ret <= 0)  // handshake failed
        return 0;

    /* Handshake events carry no payload: reserve only the header. */
    struct probe_SSL_data_t *data = bpf_ringbuf_reserve(
        &rb, __builtin_offsetof(struct probe_SSL_data_t, buf), 0);
    if (!data) {
        inc_dropped(TLS_DROP_RINGBUF_FULL);
        return 0;
    }

    data->timestamp_ns = ts;
    data->delta_ns = ts - *tsp;
    data->pid = pid;
    data->tid = tid;
    data->uid = uid;
    data->len = ret;
    data->buf_filled = 0;
    data->buf_size = 0;
    data->rw = 2;
    data->is_handshake = true;
    data->ssl_ptr = sslp ? *sslp : 0;
    bpf_get_current_comm(&data->comm, sizeof(data->comm));
    bpf_map_delete_elem(&start_ns, &tid);
    bpf_map_delete_elem(&ssls, &tid);

    /* submit to ring buffer */
    bpf_ringbuf_submit(data, 0);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";