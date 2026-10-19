// SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause)
/*
 * Contract test for include/b64.h.
 *
 * The wire format's whole point is that bytes survive. These tests therefore
 * decode every encoded result with an independent decoder in this file, rather
 * than comparing against the encoder's own arithmetic.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "b64.h"

static int failures;

static char *encode(const unsigned char *buf, size_t len)
{
	char *out = NULL;
	size_t out_len = 0;
	FILE *stream = open_memstream(&out, &out_len);

	if (!stream)
		return NULL;
	json_fprint_b64(stream, buf, len);
	fclose(stream);
	return out;
}

static int decode_char(char c)
{
	if (c >= 'A' && c <= 'Z')
		return c - 'A';
	if (c >= 'a' && c <= 'z')
		return c - 'a' + 26;
	if (c >= '0' && c <= '9')
		return c - '0' + 52;
	if (c == '+')
		return 62;
	if (c == '/')
		return 63;
	return -1;
}

/* Independent decoder; returns 0 on success. */
static int decode(const char *text, unsigned char *out, size_t *out_len)
{
	size_t n = strlen(text);
	size_t produced = 0;
	size_t i;

	if (n % 4 != 0)
		return -1;

	for (i = 0; i < n; i += 4) {
		int a = decode_char(text[i]);
		int b = decode_char(text[i + 1]);
		int c = text[i + 2] == '=' ? -2 : decode_char(text[i + 2]);
		int d = text[i + 3] == '=' ? -2 : decode_char(text[i + 3]);
		unsigned int v;

		if (a < 0 || b < 0 || c == -1 || d == -1)
			return -1;
		v = ((unsigned int)a << 18) | ((unsigned int)b << 12);
		if (c >= 0)
			v |= (unsigned int)c << 6;
		if (d >= 0)
			v |= (unsigned int)d;

		out[produced++] = (unsigned char)((v >> 16) & 0xff);
		if (c >= 0)
			out[produced++] = (unsigned char)((v >> 8) & 0xff);
		if (d >= 0)
			out[produced++] = (unsigned char)(v & 0xff);
	}
	*out_len = produced;
	return 0;
}

static void expect_encoding(const char *input, const char *expected)
{
	size_t len = strlen(input);
	char *actual = encode((const unsigned char *)input, len);

	printf("  %-10s -> %s\n", input[0] ? input : "(empty)", actual ? actual : "(null)");
	if (!actual || strcmp(actual, expected) != 0) {
		printf("    expected %s\n", expected);
		failures++;
	}
	free(actual);
}

static void test_rfc4648_vectors(void)
{
	puts("RFC 4648 vectors");
	expect_encoding("", "");
	expect_encoding("f", "Zg==");
	expect_encoding("fo", "Zm8=");
	expect_encoding("foo", "Zm9v");
	expect_encoding("foob", "Zm9vYg==");
	expect_encoding("fooba", "Zm9vYmE=");
	expect_encoding("foobar", "Zm9vYmFy");
}

static void test_all_256_bytes_roundtrip(void)
{
	unsigned char input[256];
	unsigned char output[512];
	size_t out_len = 0;
	char *encoded;
	int i;

	puts("all 256 byte values round-trip");
	for (i = 0; i < 256; i++)
		input[i] = (unsigned char)i;

	encoded = encode(input, sizeof(input));
	if (!encoded) {
		failures++;
		return;
	}
	if (strlen(encoded) != b64_encoded_len(sizeof(input))) {
		printf("  length mismatch: %zu vs %zu\n", strlen(encoded),
		       b64_encoded_len(sizeof(input)));
		failures++;
	}
	if (decode(encoded, output, &out_len) != 0 || out_len != sizeof(input) ||
	    memcmp(input, output, sizeof(input)) != 0) {
		printf("  round-trip mismatch\n");
		failures++;
	}
	free(encoded);
}

static void test_non_injective_triple(void)
{
	unsigned char b5[] = { 0xB5 };
	unsigned char e9[] = { 0xE9 };
	unsigned char c2b5[] = { 0xC2, 0xB5 };
	char *a = encode(b5, sizeof(b5));
	char *b = encode(e9, sizeof(e9));
	char *c = encode(c2b5, sizeof(c2b5));

	puts("base64 keeps 0xB5, 0xE9 and C2 B5 distinct");
	printf("  B5=%s  E9=%s  C2B5=%s\n", a, b, c);
	if (strcmp(a, b) == 0 || strcmp(a, c) == 0 || strcmp(b, c) == 0)
		failures++;
	free(a);
	free(b);
	free(c);
}

static void test_length_arithmetic(void)
{
	size_t cases[] = { 0, 1, 2, 3, 4, 597, 1275, 262144 };
	size_t expected[] = { 0, 4, 4, 4, 8, 796, 1700, 349528 };
	size_t i;

	puts("padded length arithmetic");
	for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
		size_t actual = b64_encoded_len(cases[i]);

		if (actual != expected[i]) {
			printf("  b64_encoded_len(%zu) = %zu, expected %zu\n", cases[i],
			       actual, expected[i]);
			failures++;
		}
	}
}

static void test_alphabet_and_padding(void)
{
	unsigned char input[256];
	char *encoded;
	size_t i;
	int seen_padding = 0;

	puts("alphabet is STANDARD and padding appears only at the end");
	for (i = 0; i < sizeof(input); i++)
		input[i] = (unsigned char)i;
	encoded = encode(input, sizeof(input));
	if (!encoded) {
		failures++;
		return;
	}
	for (i = 0; i < strlen(encoded); i++) {
		char c = encoded[i];

		if (c == '=') {
			seen_padding = 1;
			continue;
		}
		if (seen_padding || decode_char(c) < 0) {
			printf("  bad character %c at %zu\n", c, i);
			failures++;
			break;
		}
	}
	free(encoded);
}

int main(void)
{
	test_rfc4648_vectors();
	test_all_256_bytes_roundtrip();
	test_non_injective_triple();
	test_length_arithmetic();
	test_alphabet_and_padding();

	printf("\n%s (%d failures)\n", failures ? "FAIL" : "PASS", failures);
	return failures ? 1 : 0;
}
