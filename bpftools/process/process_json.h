// SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause)
// JSON emission for FILE_OPERATION events.
//
// Extracted from process.c so the wire format can be tested without a running
// BPF program: the consumer (collector) classifies actions by the `event`
// field, so emitting FILE_OPEN for a delete would silently record the wrong
// operation.
#ifndef PROCESS_JSON_H
#define PROCESS_JSON_H

#include <arpa/inet.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "json_escape.h"
#include "process.h"

static inline const char *file_op_event_name(int op)
{
	switch (op) {
	case FILE_OP_DELETE:
		return "FILE_DELETE";
	case FILE_OP_TRUNCATE:
		return "FILE_TRUNCATE";
	case FILE_OP_RENAME:
		return "FILE_RENAME";
	default:
		return "FILE_OPEN";
	}
}

/* Top-level EXEC mapping evidence. A namespace is only emitted when the BPF
 * side validated it and every identity component is positive; otherwise say
 * null rather than guess. The error is always reported. */
static inline void print_pid_namespace(const struct event *e)
{
	if (e->pid_namespace.error == 0 &&
	    e->pid_namespace.device > 0 &&
	    e->pid_namespace.inode > 0 &&
	    e->pid_namespace.tgid > 0) {
		printf(",\"pid_namespace\":{\"device\":%llu,\"inode\":%llu,\"tgid\":%u}",
		       (unsigned long long)e->pid_namespace.device,
		       (unsigned long long)e->pid_namespace.inode,
		       e->pid_namespace.tgid);
	} else {
		printf(",\"pid_namespace\":null");
	}
	printf(",\"pid_namespace_error\":%d", e->pid_namespace.error);
}

/* EXEC metadata captured in-kernel, so it cannot race a post-event procfs
 * lookup. Only positive values are evidence; zero means the read failed. */
static inline void print_exec_metadata(const struct event *e)
{
	print_pid_namespace(e);
	if (e->pgid > 0)
		printf(",\"pgid\":%d", e->pgid);
	else
		printf(",\"pgid\":null");
	if (e->cgroup_id > 0)
		printf(",\"cgroup_id\":%llu", (unsigned long long)e->cgroup_id);
	else
		printf(",\"cgroup_id\":null");
}

/* Kernel inventory describes observation, never execution or an action. */
static inline void print_process_snapshot(const struct event *e)
{
    printf("{\"timestamp\":%llu,\"event\":\"PROCESS_SNAPSHOT\",\"pid\":%d,\"ppid\":%d,",
           e->timestamp_ns, e->pid, e->ppid);
    printf("\"start_time_ns\":%llu,\"parent_start_time_ns\":%llu,\"comm\":",
           e->start_time_ns, e->parent_start_time_ns);
    json_print_str(e->comm);
    if (e->pgid > 0) printf(",\"pgid\":%d", e->pgid); else printf(",\"pgid\":null");
    if (e->cgroup_id) printf(",\"cgroup_id\":%llu", e->cgroup_id); else printf(",\"cgroup_id\":null");
    printf(",\"monitor_pid_namespace\":");
    if (!e->pid_namespace.error && e->pid_namespace.tgid && e->pid_namespace.inode && e->pid_namespace.device)
        printf("{\"device\":%llu,\"inode\":%llu,\"tgid\":%u}",
               e->pid_namespace.device, e->pid_namespace.inode, e->pid_namespace.tgid);
    else printf("null");
    printf(",\"monitor_pid_namespace_error\":%d}\n", e->pid_namespace.error);
    fflush(stdout);
}

/* Completion facts are separate from intent and from payload effects. */
static inline const char *syscall_outcome(const struct event *e)
{
    if (!e->syscall_completed)
        return "unknown";
    if (e->type == EVENT_TYPE_NETWORK_OPERATION &&
        e->net_op.op == NET_OP_CONNECT &&
        (e->syscall_ret == -115 || e->syscall_ret == -114))
        return "pending";
    return e->syscall_ret >= 0 ? "succeeded" : "failed";
}

static inline void print_syscall_result(const struct event *e)
{
    printf(",\"ppid\":%d,\"outcome\":\"%s\",\"syscall_ret\":", e->ppid, syscall_outcome(e));
    if (e->syscall_completed)
        printf("%lld", e->syscall_ret);
    else
        printf("null");
}

static inline void print_file_event(const struct event *e, uint64_t timestamp_ns,
				    uint32_t count, const char *extra_fields)
{
	printf("{");
	printf("\"timestamp\":%llu,", (unsigned long long)timestamp_ns);
	printf("\"event\":\"%s\",", file_op_event_name(e->file_op.op));
	printf("\"comm\":");
	json_print_str(e->comm);
	printf(",");
	printf("\"pid\":%d,", e->pid);
	printf("\"count\":%u,", count);
	printf("\"filepath\":");
	json_print_str(e->file_op.filepath);
	printf(",");
	printf("\"flags\":%d", e->file_op.flags);
	if (e->file_op.op == FILE_OP_RENAME) {
		printf(",\"filepath2\":");
		json_print_str(e->file_op.filepath2);
	}
	printf(",\"start_time_ns\":%llu", (unsigned long long)e->start_time_ns);
	printf(",\"parent_start_time_ns\":%llu",
	       (unsigned long long)e->parent_start_time_ns);

	print_syscall_result(e);
	if (extra_fields && strlen(extra_fields) > 0) {
		printf(",%s", extra_fields);
	}

	printf("}\n");
	fflush(stdout);
}

/* ── Network events ─────────────────────────────────────────────────────── */

/* Kept as literal returns (not a table) so the coverage contract in
 * collector/tests/coverage_contract.rs can see that these event types have a
 * producer — the emitter never builds the name from a numeric enum alone. */
static inline const char *net_event_name(unsigned char op)
{
	switch (op) {
	case NET_OP_SENDTO:
		return "NETWORK_SEND";
	default:
		return "NETWORK_CONNECT";
	}
}

static inline const char *net_family_name(unsigned short family)
{
	switch (family) {
	case MTL_AF_INET:
		return "AF_INET";
	case MTL_AF_INET6:
		return "AF_INET6";
	default:
		return "unknown";
	}
}

/* Render the four address bytes in network order. Going through the byte
 * representation keeps this independent of the host's integer endianness:
 * `addr` was copied from the sockaddr's `s_addr`, whose memory layout is the
 * wire order. */
static inline void net_format_ipv4(unsigned int addr, char *buf, size_t len)
{
	const unsigned char *b = (const unsigned char *)&addr;

	snprintf(buf, len, "%u.%u.%u.%u", (unsigned)b[0], (unsigned)b[1],
		 (unsigned)b[2], (unsigned)b[3]);
}

static inline void net_format_ipv6(const unsigned char addr[16], char *buf,
				   size_t len)
{
	if (!inet_ntop(AF_INET6, addr, buf, (socklen_t)len))
		snprintf(buf, len, "::");
}

static inline void print_network_event(const struct event *e, uint64_t timestamp_ns)
{
	printf("{");
	printf("\"timestamp\":%llu,", (unsigned long long)timestamp_ns);
	printf("\"event\":\"%s\",", net_event_name(e->net_op.op));
	printf("\"comm\":");
	json_print_str(e->comm);
	printf(",");
	printf("\"pid\":%d,", e->pid);
	printf("\"start_time_ns\":%llu", (unsigned long long)e->start_time_ns);
	printf(",\"parent_start_time_ns\":%llu",
	       (unsigned long long)e->parent_start_time_ns);

	print_syscall_result(e);
	printf(",\"net_op\":{");
	printf("\"family\":\"%s\"", net_family_name(e->net_op.family));
	if (e->net_op.supported) {
		/* INET6_ADDRSTRLEN covers the longest IPv6 form. */
		char host[INET6_ADDRSTRLEN];

		if (e->net_op.family == MTL_AF_INET6)
			net_format_ipv6(e->net_op.addr6, host, sizeof(host));
		else
			net_format_ipv4(e->net_op.addr, host, sizeof(host));
		printf(",\"host\":\"%s\",\"port\":%u", host,
		       (unsigned)e->net_op.port);
	} else {
		/* The address could not be decoded (short addrlen or an
		 * unreadable buffer). Saying so is the honest option;
		 * emitting a placeholder address would be a lie the action
		 * layer cannot detect. */
		printf(",\"unsupported_family\":true");
	}
	printf("}}");
	printf("\n");
	fflush(stdout);
}

#endif /* PROCESS_JSON_H */