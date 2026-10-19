/* SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause) */
/*
 * Emit the payload fields of a tls_capture event.
 *
 * Contract:
 *   "data"      human-readable / legacy field, escaped exactly as before
 *   "data_b64"  authoritative bytes (RFC 4648 STANDARD, '=' padding)
 *   a string means bytes were captured; an explicit null means none were
 *   absent field (in old captures) means legacy format
 *   "truncated"/"bytes_lost" are counted in raw bytes, never in base64 chars
 *
 * Keeping both fields is deliberate: existing consumers read `data` and must
 * keep working, while P1's byte-exact path reads `data_b64`. Encoding is
 * streamed to the FILE* so the caller's fixed event buffer is unchanged and
 * the BPF struct never grows.
 */
#ifndef __PAYLOAD_JSON_H
#define __PAYLOAD_JSON_H

#include <stddef.h>
#include <stdio.h>

#include "b64.h"
#include "json_escape.h"

static inline void json_print_payload_fields(FILE *out, const unsigned char *buf,
					     size_t buf_size, unsigned int declared_len)
{
	if (buf_size > 0) {
		fputs("\"data\":", out);
		json_fprint_escaped(out, (const char *)buf, buf_size);
		fputs(",\"data_b64\":\"", out);
		json_fprint_b64(out, buf, buf_size);
		fputs("\",", out);
	} else {
		fputs("\"data\":null,\"data_b64\":null,", out);
	}

	if (buf_size < (size_t)declared_len) {
		fprintf(out, "\"truncated\":true,\"bytes_lost\":%u",
			declared_len - (unsigned int)buf_size);
	} else {
		fputs("\"truncated\":false", out);
	}
}

#endif /* __PAYLOAD_JSON_H */
