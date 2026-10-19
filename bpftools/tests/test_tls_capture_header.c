// SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause)
/*
 * Contract test for tls_capture.h data structures.
 *
 * Guarantees:
 *   - probe_SSL_data_t carries ssl_ptr (TLS connection identity)
 *   - Existing field offsets are unchanged (BPF and user space share this struct)
 *   - ssl_ptr sits after buf, so the variable-length data area keeps its layout
 *
 * Layout (verified on this platform):
 *   timestamp_ns=0  delta_ns=8  pid=16  tid=20  uid=24  len=28
 *   buf_size=32     buf_filled=36  rw=40  comm=44  buf=60
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

/* ---- BPF type stubs --------------------------------------------------- */
#ifdef __linux__
#include <linux/types.h>
#else
typedef uint32_t __u32;
typedef uint64_t __u64;
typedef uint8_t __u8;
#endif

#ifndef TASK_COMM_LEN
#define TASK_COMM_LEN 16
#endif

/* MAX_BUF_SIZE from tls_capture.h is 256KB; pull the real header so the test
 * fails to compile if the struct diverges from what BPF emits. */
#include "tls_capture.h"

/* ---- test harness ------------------------------------------------------ */
#define RESET  "\033[0m"
#define RED    "\033[31m"
#define GREEN  "\033[32m"

static int tests_passed = 0;
static int tests_failed = 0;

static void test_assert(bool condition, const char *test_name)
{
	if (condition) {
		printf("[" GREEN "PASS" RESET "] %s\n", test_name);
		tests_passed++;
	} else {
		printf("[" RED "FAIL" RESET "] %s\n", test_name);
		tests_failed++;
	}
}

int main(void)
{
	/* static: the struct embeds a 256KB buffer; keep it off the stack. */
	static struct probe_SSL_data_t e;

	printf("== tls_capture.h contract tests ==\n");

	/* Existing offsets must not move — BPF and user space share this struct. */
	test_assert(offsetof(struct probe_SSL_data_t, timestamp_ns) == 0,
		    "timestamp_ns stays at offset 0");
	test_assert(offsetof(struct probe_SSL_data_t, pid) == 16,
		    "pid stays at offset 16");
	test_assert(offsetof(struct probe_SSL_data_t, comm) == 44,
		    "comm stays at offset 44");
	test_assert(offsetof(struct probe_SSL_data_t, buf) == 60,
		    "buf stays at offset 60 (data area layout preserved)");

	/* New field must exist and be readable/writable. */
	e.ssl_ptr = 0xdeadbeefcafeULL;
	test_assert(e.ssl_ptr == 0xdeadbeefcafeULL, "ssl_ptr is readable/writable");
	test_assert(sizeof(e.ssl_ptr) == 8, "ssl_ptr is 64-bit");

	/* ssl_ptr must sit after buf so the variable-length area is untouched. */
	test_assert(offsetof(struct probe_SSL_data_t, buf) <
		    offsetof(struct probe_SSL_data_t, ssl_ptr),
		    "ssl_ptr is placed after buf");

	/*
	 * Size contract. These numbers are load-bearing for P1's base64
	 * decision: 262144 raw bytes encode to 349528 characters, which is why
	 * encoding happens in user space instead of inside this struct (a
	 * base64 field here would grow the struct and cut ring occupancy).
	 */
	test_assert(MAX_BUF_SIZE == 256 * 1024, "MAX_BUF_SIZE is 256 KiB");
	test_assert(RING_BUFFER_SIZE == 8 * 1024 * 1024, "RING_BUFFER_SIZE is 8 MiB");

	printf("\n%d passed, %d failed\n", tests_passed, tests_failed);
	return tests_failed == 0 ? 0 : 1;
}
