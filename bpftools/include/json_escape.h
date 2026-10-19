/* SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause) */
/*
 * Shared JSON string emission for every bpftools program.
 *
 * Why this exists as a shared header:
 *
 *   - Four divergent copies of this logic had accumulated (tls_capture,
 *     browsertrace, stdiocap, process_ext/map_flush.h). They disagreed:
 *     map_flush did not escape control characters, and stdiocap's UTF-8
 *     validator lacked the overlong / surrogate checks, so it emitted raw
 *     surrogate code points — producing JSON that serde_json rejects.
 *
 *   - Several fields were printed with a bare `printf("%s")`, including
 *     `comm` and `filepath`. The consumer (collector's runner/common.rs)
 *     requires each line to be valid JSON and DROPS the whole line on a
 *     parse error. `filepath` comes from files an agent itself creates, so a
 *     quoted filename is not a theoretical risk — it silently deleted events.
 *
 * Everything here writes to stdout and is safe for arbitrary byte input.
 */
#ifndef __JSON_ESCAPE_H
#define __JSON_ESCAPE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

/*
 * Validate one UTF-8 sequence at `str`, with at most `remaining` bytes.
 *
 * Returns the sequence length (1..4), or 0 if invalid. Rejects overlong
 * encodings, UTF-16 surrogate code points (U+D800..U+DFFF) and out-of-range
 * code points — all of which would make the emitted JSON invalid.
 */
static inline int json_validate_utf8_char(const unsigned char *str, size_t remaining)
{
	unsigned char c;
	int expected_len;

	if (!str || remaining == 0)
		return 0;

	c = str[0];

	if (c < 0x80)
		return 1;

	if ((c & 0xE0) == 0xC0)
		expected_len = 2; /* 110xxxxx */
	else if ((c & 0xF0) == 0xE0)
		expected_len = 3; /* 1110xxxx */
	else if ((c & 0xF8) == 0xF0)
		expected_len = 4; /* 11110xxx */
	else
		return 0; /* invalid start byte */

	if (remaining < (size_t)expected_len)
		return 0;

	/* Continuation bytes must all be 10xxxxxx. */
	{
		int i;

		for (i = 1; i < expected_len; i++) {
			if ((str[i] & 0xC0) != 0x80)
				return 0;
		}
	}

	if (expected_len == 2) {
		unsigned int codepoint = ((c & 0x1F) << 6) | (str[1] & 0x3F);

		if (codepoint < 0x80)
			return 0; /* overlong */
	} else if (expected_len == 3) {
		unsigned int codepoint = ((c & 0x0F) << 12) |
					 ((str[1] & 0x3F) << 6) |
					 (str[2] & 0x3F);

		if (codepoint < 0x800)
			return 0; /* overlong */
		if (codepoint >= 0xD800 && codepoint <= 0xDFFF)
			return 0; /* surrogate */
	} else if (expected_len == 4) {
		unsigned int codepoint = ((c & 0x07) << 18) |
					 ((str[1] & 0x3F) << 12) |
					 ((str[2] & 0x3F) << 6) |
					 (str[3] & 0x3F);

		if (codepoint < 0x10000 || codepoint > 0x10FFFF)
			return 0; /* out of range */
	}

	return expected_len;
}

/*
 * Print `len` bytes of `buf` as a complete JSON string literal (quotes
 * included). Bytes that cannot be represented in UTF-8 are emitted as
 * \u00XX escapes, so the result is always valid JSON.
 */
static inline void json_fprint_escaped(FILE *out, const char *buf, size_t len)
{
	size_t i;

	fputc('"', out);
	for (i = 0; i < len; i++) {
		unsigned char c = (unsigned char)buf[i];

		switch (c) {
		case '"':
		case '\\':
			fprintf(out, "\\%c", c);
			break;
		case '\n':
			fputs("\\n", out);
			break;
		case '\r':
			fputs("\\r", out);
			break;
		case '\t':
			fputs("\\t", out);
			break;
		case '\b':
			fputs("\\b", out);
			break;
		case '\f':
			fputs("\\f", out);
			break;
		default:
			if (c >= 32 && c <= 126) {
				fputc(c, out);
			} else if (c >= 128) {
				int n = json_validate_utf8_char(
					(const unsigned char *)&buf[i], len - i);

				if (n > 0) {
					int j;

					for (j = 0; j < n; j++)
						fputc(buf[i + j], out);
					i += (size_t)n - 1;
				} else {
					fprintf(out, "\\u%04x", c);
				}
			} else {
				fprintf(out, "\\u%04x", c);
			}
			break;
		}
	}
	fputc('"', out);
}

/* stdout wrapper kept so every existing call site is unchanged. */
static inline void json_print_escaped(const char *buf, size_t len)
{
	json_fprint_escaped(stdout, buf, len);
}

/* Convenience wrapper for NUL-terminated input. */
static inline void json_print_str(const char *s)
{
	json_print_escaped(s, s ? strlen(s) : 0);
}

/*
 * Emit `"<key>":<escaped-string>,` in one call.
 *
 * Use this for every string field whose value can contain attacker- or
 * agent-controlled bytes (`comm`, `filepath`, `filename`, `full_command`, ...).
 */
static inline void json_emit_str_field(const char *key, const char *value)
{
	putchar('"');
	fputs(key, stdout);
	printf("\":");
	json_print_str(value);
	putchar(',');
}

#endif /* __JSON_ESCAPE_H */
