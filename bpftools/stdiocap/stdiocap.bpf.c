// SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
#include <vmlinux.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include "stdiocap.h"
#include <asm/unistd.h>

struct io_args_t {
	__u64 buf;
	__u64 start_ns;
	__s32 fd;
	__u8 is_read;
};

struct {
	__uint(type, BPF_MAP_TYPE_RINGBUF);
	__uint(max_entries, RING_BUFFER_SIZE);
} rb SEC(".maps");

struct {
	__uint(type, BPF_MAP_TYPE_HASH);
	__uint(max_entries, 8192);
	__type(key, __u64);
	__type(value, struct io_args_t);
} io_args SEC(".maps");

/* Ring-buffer reservation failures were silent: the event simply vanished.
 * A capture channel that silently drops events cannot be used to claim that
 * "nothing was observed". */
struct {
	__uint(type, BPF_MAP_TYPE_ARRAY);
	__uint(max_entries, STDIO_DROP_REASON_MAX);
	__type(key, __u32);
	__type(value, __u64);
} dropped_events SEC(".maps");

static __always_inline void inc_dropped(enum stdio_drop_reason reason)
{
	__u32 key = (__u32)reason;
	__u64 *value = bpf_map_lookup_elem(&dropped_events, &key);

	if (value)
		__sync_fetch_and_add(value, 1);
}

const volatile __u32 targ_pid = 0;
const volatile __u32 targ_uid = 0xffffffffU;
const volatile bool trace_stdio_only = true;
const volatile __u32 max_capture_bytes = MAX_BUF_SIZE;

static __always_inline bool target_allowed(__u32 uid, __u32 pid)
{
	if (targ_pid && targ_pid != pid)
		return false;
	if (targ_uid != 0xffffffffU && targ_uid != uid)
		return false;
	return true;
}

static __always_inline int enter_common(int fd, const void *buf, bool is_read)
{
	__u64 pid_tgid = bpf_get_current_pid_tgid();
	__u32 pid = pid_tgid >> 32;
	__u32 uid = bpf_get_current_uid_gid();
	struct io_args_t args = {};

	if (!target_allowed(uid, pid))
		return 0;

	args.buf = (__u64)buf;
	args.start_ns = bpf_ktime_get_ns();
	args.fd = fd;
	args.is_read = is_read;
	if (bpf_map_update_elem(&io_args, &pid_tgid, &args, BPF_ANY)) {
		inc_dropped(STDIO_DROP_ARGS_MAP_FAILED);
		return 0;
	}
	return 0;
}

static __always_inline int exit_common(long ret)
{
	__u64 pid_tgid = bpf_get_current_pid_tgid();
	__u32 pid = pid_tgid >> 32;
	__u32 tid = (__u32)pid_tgid;
	__u32 uid = bpf_get_current_uid_gid();
	struct io_args_t *args;
	struct stdiocap_event_t *event;
	__u32 copy_size;

	/* Only the target's read/write exits can have a pending entry. Without
	 * this check every unrelated syscall on the host looked like a lost
	 * event (19,894 "misses" in a 4s benign run). */
	if (!target_allowed(uid, pid))
		return 0;

	args = bpf_map_lookup_elem(&io_args, &pid_tgid);
	if (!args) {
		/* The enter-side arguments are missing: this read/write is lost. */
		inc_dropped(STDIO_DROP_LOOKUP_MISSED);
		return 0;
	}

	/* The enter side stores every read/write for the target so the exit can
	 * decide by fd; an untracked fd is filtered here, not counted as loss. */
	if (trace_stdio_only && (args->fd < 0 || args->fd > 2)) {
		bpf_map_delete_elem(&io_args, &pid_tgid);
		return 0;
	}

	if (ret <= 0) {
		bpf_map_delete_elem(&io_args, &pid_tgid);
		return 0;
	}

	event = bpf_ringbuf_reserve(&rb, sizeof(*event), 0);
	if (!event) {
		inc_dropped(STDIO_DROP_RINGBUF_FULL);
		bpf_map_delete_elem(&io_args, &pid_tgid);
		return 0;
	}

	event->timestamp_ns = bpf_ktime_get_ns();
	event->delta_ns = event->timestamp_ns - args->start_ns;
	event->pid = pid;
	event->tid = tid;
	event->uid = uid;
	event->fd = args->fd;
	event->len = ret > 0xffffffffL ? 0xffffffffU : (__u32)ret;
	event->is_read = args->is_read;
	bpf_get_current_comm(&event->comm, sizeof(event->comm));

	copy_size = event->len;
	if (copy_size > max_capture_bytes)
		copy_size = max_capture_bytes;
	if (copy_size > MAX_BUF_SIZE)
		copy_size = MAX_BUF_SIZE;
	event->buf_size = copy_size;

	event->copy_failed = 0;
	if (copy_size > 0) {
		long copied = bpf_probe_read_user(event->buf, copy_size,
						  (const void *)args->buf);

		if (copied < 0) {
			/* Never let uninitialised ring-buffer bytes look like
			 * content: zero the length and record the failure. */
			event->copy_failed = 1;
			event->buf_size = 0;
		}
	}

	bpf_ringbuf_submit(event, 0);
	bpf_map_delete_elem(&io_args, &pid_tgid);
	return 0;
}

/* Use the raw syscall tracepoints, not the per-syscall `syscalls:*` ones.
 * On Linux 7.0 the per-syscall tracepoints did not fire for targets spawned
 * by the benchmark harness while `raw_syscalls` did (the process helper has
 * always used raw). Raw tracepoints are also available on every kernel we
 * support, so capture does not silently go blind on a different host. */
SEC("tp/raw_syscalls/sys_enter")
int trace_raw_enter(struct trace_event_raw_sys_enter *ctx)
{
	if (ctx->id == __NR_write)
		return enter_common((int)ctx->args[0], (const void *)ctx->args[1], false);
	if (ctx->id == __NR_read)
		return enter_common((int)ctx->args[0], (const void *)ctx->args[1], true);
	return 0;
}

SEC("tp/raw_syscalls/sys_exit")
int trace_raw_exit(struct trace_event_raw_sys_exit *ctx)
{
	if (ctx->id != __NR_read && ctx->id != __NR_write)
		return 0;
	return exit_common(ctx->ret);
}

char LICENSE[] SEC("license") = "Dual BSD/GPL";
