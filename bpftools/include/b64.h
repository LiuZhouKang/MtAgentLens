/* SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause) */
/*
 * Streaming base64 (RFC 4648 STANDARD alphabet with '=' padding).
 *
 * Why this exists: tls_capture's `data` field escapes bytes it cannot render
 * as UTF-8 with a backslash-u escape. A JSON decoder maps that escape back to
 * the UTF-8 encoding of a code point, so one captured byte becomes two bytes
 * and the original value is gone. base64 is injective: the same bytes always
 * decode to the same bytes, and a length check catches corruption.
 *
 * This encoder writes directly to a FILE* and never allocates, so the
 * caller's fixed-size event buffer (MAX_BUF_SIZE + 1) stays unchanged.
 * Encoding happens in user space; the BPF structs in probe_SSL_data_t are
 * deliberately untouched (a base64 field inside the struct would grow it by
 * 87 KB and cut ring occupancy).
 */
#ifndef __B64_H
#define __B64_H

#include <stddef.h>
#include <stdio.h>

/* RFC 4648 padded length: 4 characters per 3 input bytes. */
static inline size_t b64_encoded_len(size_t len)
{
	return 4 * ((len + 2) / 3);
}

/* Emit `len` bytes of `buf` as base64 (no quotes, no newline). */
static inline void json_fprint_b64(FILE *out, const unsigned char *buf, size_t len)
{
	static const char table[] =
		"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
	size_t i = 0;

	for (; i + 3 <= len; i += 3) {
		unsigned int v = ((unsigned int)buf[i] << 16) |
				 ((unsigned int)buf[i + 1] << 8) |
				 (unsigned int)buf[i + 2];

		fputc(table[(v >> 18) & 0x3f], out);
		fputc(table[(v >> 12) & 0x3f], out);
		fputc(table[(v >> 6) & 0x3f], out);
		fputc(table[v & 0x3f], out);
	}

	if (len - i == 1) {
		unsigned int v = (unsigned int)buf[i] << 16;

		fputc(table[(v >> 18) & 0x3f], out);
		fputc(table[(v >> 12) & 0x3f], out);
		fputc('=', out);
		fputc('=', out);
	} else if (len - i == 2) {
		unsigned int v = ((unsigned int)buf[i] << 16) |
				 ((unsigned int)buf[i + 1] << 8);

		fputc(table[(v >> 18) & 0x3f], out);
		fputc(table[(v >> 12) & 0x3f], out);
		fputc(table[(v >> 6) & 0x3f], out);
		fputc('=', out);
	}
}

#endif /* __B64_H */
