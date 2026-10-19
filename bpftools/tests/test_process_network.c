// SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause)
#define _GNU_SOURCE
/*
 * Contract test for the NETWORK_CONNECT / NETWORK_SEND JSON line.
 *
 * The collector classifies actions from these lines (`routing.rs` →
 * `ActionClassifier`), so the wire format is a contract:
 *   - `event` distinguishes connect from sendto,
 *   - `net_op.family` is a name the consumer can switch on,
 *   - `net_op.host`/`net_op.port` exist only when the address family was
 *     actually parsed (AF_INET). An AF_INET6 address must be reported as
 *     `unsupported_family: true` with no fabricated host.
 *
 * The test runs without a kernel: it feeds a struct event to the same
 * emitter the userspace program calls.
 */
#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "process.h"
#include "process_json.h"

static char *capture_network(const struct event *e)
{
	char *result = NULL;
	size_t result_len = 0;
	FILE *saved = stdout;
	FILE *stream = open_memstream(&result, &result_len);

	if (!stream)
		return NULL;
	stdout = stream;
	print_network_event(e, e->timestamp_ns);
	fflush(stdout);
	stdout = saved;
	fclose(stream);
	return result;
}

static struct event base_event(void)
{
	struct event e;

	memset(&e, 0, sizeof(e));
	e.type = EVENT_TYPE_NETWORK_OPERATION;
	e.pid = 4242;
	e.timestamp_ns = 1700000000000000000ULL;
	e.start_time_ns = 100;
	e.parent_start_time_ns = 50;
	snprintf(e.comm, sizeof(e.comm), "curl");
	return e;
}

static int contains(const char *haystack, const char *needle)
{
	return strstr(haystack, needle) != NULL;
}

int main(void)
{
	int failures = 0;
	char *line;

	/* 1. AF_INET connect: dotted decimal + host-order port. */
	{
		struct event e = base_event();
		e.net_op.op = NET_OP_CONNECT;
		e.net_op.family = 2; /* AF_INET */
		e.net_op.supported = 1;
		e.net_op.addr = htonl(0x7f000001); /* 127.0.0.1 */
		e.net_op.port = 8080;

		line = capture_network(&e);
		printf("  connect:  %s", line ? line : "(null)\n");
		if (!line || !contains(line, "\"event\":\"NETWORK_CONNECT\"") ||
		    !contains(line, "\"family\":\"AF_INET\"") ||
		    !contains(line, "\"host\":\"127.0.0.1\"") ||
		    !contains(line, "\"port\":8080") ||
		    !contains(line, "\"pid\":4242") ||
		    !contains(line, "\"start_time_ns\":100") ||
		    !contains(line, "\"parent_start_time_ns\":50"))
			failures++;
		free(line);
	}

	/* 2. AF_INET sendto: different event name, address with high bytes. */
	{
		struct event e = base_event();
		e.net_op.op = NET_OP_SENDTO;
		e.net_op.family = 2;
		e.net_op.supported = 1;
		e.net_op.addr = htonl(0xc0a80101); /* 192.168.1.1 */
		e.net_op.port = 53;

		line = capture_network(&e);
		printf("  sendto:   %s", line ? line : "(null)\n");
		if (!line || !contains(line, "\"event\":\"NETWORK_SEND\"") ||
		    !contains(line, "\"host\":\"192.168.1.1\"") ||
		    !contains(line, "\"port\":53"))
			failures++;
		free(line);
	}

	/* 3. AF_INET6 is decoded: `::1` at port 443. */
	{
		struct event e = base_event();
		e.net_op.op = NET_OP_CONNECT;
		e.net_op.family = 10; /* AF_INET6 */
		e.net_op.supported = 1;
		memset(e.net_op.addr6, 0, sizeof(e.net_op.addr6));
		e.net_op.addr6[15] = 1; /* ::1 */
		e.net_op.port = 443;

		line = capture_network(&e);
		printf("  ipv6:     %s", line ? line : "(null)\n");
		if (!line || !contains(line, "\"family\":\"AF_INET6\"") ||
		    !contains(line, "\"host\":\"::1\"") ||
		    !contains(line, "\"port\":443") ||
		    contains(line, "\"unsupported_family\""))
			failures++;
		free(line);
	}

	/* 3b. An undecodable address (short addrlen) is marked, not faked. */
	{
		struct event e = base_event();
		e.net_op.op = NET_OP_CONNECT;
		e.net_op.family = 10;
		e.net_op.supported = 0;

		line = capture_network(&e);
		printf("  short:    %s", line ? line : "(null)\n");
		if (!line || !contains(line, "\"unsupported_family\":true") ||
		    contains(line, "\"host\""))
			failures++;
		free(line);
	}

	/* 4. comm is attacker-influenced text: it must be escaped. */
	{
		struct event e = base_event();
		snprintf(e.comm, sizeof(e.comm), "a\"b");
		e.net_op.op = NET_OP_CONNECT;
		e.net_op.family = 2;
		e.net_op.supported = 1;
		e.net_op.addr = htonl(0x08080808);
		e.net_op.port = 443;

		line = capture_network(&e);
		printf("  escape:   %s", line ? line : "(null)\n");
		if (!line || !contains(line, "\"comm\":\"a\\\"b\""))
			failures++;
		free(line);
	}

	printf("\n%s (%d 项失败)\n", failures ? "FAIL" : "PASS", failures);
	return failures ? 1 : 0;
}
