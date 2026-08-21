// SPDX-License-Identifier: GPL-2.0 OR BSD-3-Clause
#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <bpf/bpf_core_read.h>
#include "fileaccess.h"

char LICENSE[] SEC("license") = "Dual BSD/GPL";

struct {
	__uint(type, BPF_MAP_TYPE_RINGBUF);
	__uint(max_entries, 256 * 1024);
} rb SEC(".maps");

const volatile pid_t targ_pid = 0;

static __always_inline int trace_open(const char *filename, int flags, int mode, int op_type)
{
	struct fileaccess_event *e;
	pid_t pid = bpf_get_current_pid_tgid() >> 32;
	uid_t uid = bpf_get_current_uid_gid() & 0xFFFFFFFF;

	if (targ_pid && pid != targ_pid)
		return 0;

	e = bpf_ringbuf_reserve(&rb, sizeof(*e), 0);
	if (!e)
		return 0;

	e->timestamp_ns = bpf_ktime_get_ns();
	e->pid = pid;
	e->uid = uid;
	e->op_type = op_type;
	e->flags = flags;
	e->mode = mode;
	bpf_get_current_comm(&e->comm, sizeof(e->comm));
	bpf_probe_read_kernel_str(e->filename, sizeof(e->filename), (void *)filename);

	bpf_ringbuf_submit(e, 0);
	return 0;
}

SEC("tracepoint/syscalls/sys_enter_open")
int trace_open_entry(struct trace_event_raw_sys_enter *ctx)
{
	const char *filename = (const char *)ctx->args[0];
	int flags = ctx->args[1];
	int mode = ctx->args[2];
	return trace_open(filename, flags, mode, FILEACCESS_OPEN);
}

SEC("tracepoint/syscalls/sys_enter_openat")
int trace_openat_entry(struct trace_event_raw_sys_enter *ctx)
{
	const char *filename = (const char *)ctx->args[1];
	int flags = ctx->args[2];
	int mode = ctx->args[3];
	return trace_open(filename, flags, mode, FILEACCESS_OPENAT);
}
