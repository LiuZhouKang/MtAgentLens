// SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause)
/*
 * Characterization test for include/payload_json.h.
 *
 * The escape rules themselves are covered exhaustively by test_json_escape
 * (fprint == print for every byte value). This file pins the *field contract*:
 * both fields appear in order, base64 is exact, null means "nothing captured",
 * and truncation is counted in raw bytes.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "payload_json.h"

static int failures;

static char *render(const unsigned char *buf, size_t len, unsigned int declared)
{
	char *out = NULL;
	size_t out_len = 0;
	FILE *stream = open_memstream(&out, &out_len);

	if (!stream)
		return NULL;
	json_print_payload_fields(stream, buf, len, declared);
	fclose(stream);
	return out;
}

static void expect_contains(const char *name, const unsigned char *buf, size_t len,
			    unsigned int declared, const char *needle)
{
	char *out = render(buf, len, declared);

	printf("  %-24s %s\n", name, out ? out : "(null)");
	if (!out || strstr(out, needle) == NULL) {
		printf("    missing: %s\n", needle);
		failures++;
	}
	free(out);
}

static void expect_absent(const char *name, const unsigned char *buf, size_t len,
			  unsigned int declared, const char *needle)
{
	char *out = render(buf, len, declared);

	if (!out || strstr(out, needle) != NULL) {
		printf("  %-24s unexpected: %s\n", name, needle);
		failures++;
	}
	free(out);
}

int main(void)
{
	const unsigned char empty[] = { 0 };
	const unsigned char abc[] = { 'a', 'b', 'c' };
	const unsigned char quoted[] = { 'a', '"', 'b', '\\', 'c' };
	const unsigned char binary[] = { 0x00, 0x0A, 0x1F, 0x7F, 0xB5, 0xC2, 0xB5 };

	puts("payload field contract");

	expect_contains("empty payload", empty, 0, 0,
			"\"data\":null,\"data_b64\":null,\"truncated\":false");

	expect_contains("unread bytes are reported", empty, 0, 5,
			"\"data\":null,\"data_b64\":null,\"truncated\":true,\"bytes_lost\":5");

	expect_contains("ascii payload", abc, 3, 3,
			"\"data\":\"abc\",\"data_b64\":\"YWJj\",\"truncated\":false");

	expect_contains("truncated payload", abc, 3, 5,
			"\"data_b64\":\"YWJj\",\"truncated\":true,\"bytes_lost\":2");

	expect_contains("quotes and backslash", quoted, 5, 5,
			"\"data_b64\":\"YSJiXGM=\"");

	expect_contains("binary payload", binary, 7, 7,
			"\"data_b64\":\"AAoff7XCtQ==\"");
	expect_contains("binary payload keeps data", binary, 7, 7,
			"\"data\":\"");
	expect_absent("binary payload is not null", binary, 7, 7,
		      "\"data\":null");

	printf("\n%s (%d failures)\n", failures ? "FAIL" : "PASS", failures);
	return failures ? 1 : 0;
}
