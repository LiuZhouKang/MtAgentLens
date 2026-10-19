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