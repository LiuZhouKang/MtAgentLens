// SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause)
#define _GNU_SOURCE
/*
 * Contract test for include/json_escape.h.
 *
 * Every bpftools program emits JSON lines that the collector parses with
 * serde_json; a single malformed line is silently dropped by the consumer.
 * Fields like `comm` and `filepath` carry bytes the monitored agent controls,
 * so escaping must hold for arbitrary input — including quotes, control
 * characters, invalid UTF-8 and UTF-16 surrogate code points.
 */
#include <stdio.h>
#include <string.h>
#include <assert.h>
#include "json_escape.h"

static char out[4096];
static size_t out_len;

/* 把 stdout 重定向到缓冲区，捕获 json_print_escaped 的输出 */
static void capture(const char *buf, size_t len)
{
	FILE *f = fopen("/tmp/je_out.txt", "w");
	int saved;

	fflush(stdout);          /* 关键：先冲掉调用者缓冲，否则会污染捕获文件 */
	saved = dup(fileno(stdout));
	dup2(fileno(f), fileno(stdout));
	json_print_escaped(buf, len);
	fflush(stdout);
	fflush(stdout);
	dup2(saved, fileno(stdout));
	close(saved);
	fclose(f);
}

static const char *read_out(void)
{
	FILE *f = fopen("/tmp/je_out.txt", "r");
	out_len = fread(out, 1, sizeof(out) - 1, f);
	out[out_len] = '\0';
	fclose(f);
	return out;
}

/* Capture json_fprint_escaped into a heap string (caller frees). */
static char *capture_fprint(const char *buf, size_t len)
{
	char *result = NULL;
	size_t result_len = 0;
	FILE *stream = open_memstream(&result, &result_len);

	if (!stream)
		return NULL;
	json_fprint_escaped(stream, buf, len);
	fclose(stream);
	return result;
}

/*
 * The FILE* refactor must not change a single byte. Compare every possible
 * single input byte, plus the multibyte sequences the pinned wire fixtures
 * depend on (valid UTF-8, a rejected surrogate, and lone high bytes).
 */
static int fprint_matches_print(void)
{
	int mismatches = 0;
	int b;

	for (b = 0; b < 256; b++) {
		char one = (char)b;
		char *via_file = capture_fprint(&one, 1);

		capture(&one, 1);
		if (!via_file || strcmp(read_out(), via_file) != 0)
			mismatches++;
		free(via_file);
	}

	{
		char valid_utf8[] = { (char)0xE4, (char)0xB8, (char)0xAD };
		char surrogate[] = { (char)0xED, (char)0xA0, (char)0x80 };
		char high_bytes[] = { (char)0xB5, (char)0xE9, (char)0xC2, (char)0xB5 };
		struct {
			const char *bytes;
			size_t len;
		} sequences[] = {
			{ valid_utf8, sizeof(valid_utf8) },
			{ surrogate, sizeof(surrogate) },
			{ high_bytes, sizeof(high_bytes) },
			{ "a\"b\\c\nd", 8 },
		};
		size_t i;

		for (i = 0; i < sizeof(sequences) / sizeof(sequences[0]); i++) {
			char *via_file = capture_fprint(sequences[i].bytes, sequences[i].len);

			capture(sequences[i].bytes, sequences[i].len);
			if (!via_file || strcmp(read_out(), via_file) != 0)
				mismatches++;
			free(via_file);
		}
	}

	return mismatches;
}

int main(void)
{
	int failures = 0;

	/* 1. 普通字符串 */
	capture("hello", 5);
	printf("  plain:        %s\n", read_out());
	if (strcmp(read_out(), "\"hello\"") != 0) failures++;

	/* 2. 含双引号 —— 这正是 filepath 攻击面 */
	capture("a\"b", 3);
	printf("  quote:        %s\n", read_out());
	if (strcmp(read_out(), "\"a\\\"b\"") != 0) failures++;

	/* 3. 含反斜杠 */
	capture("a\\b", 3);
	printf("  backslash:    %s\n", read_out());
	if (strcmp(read_out(), "\"a\\\\b\"") != 0) failures++;

	/* 4. 控制字符 */
	capture("a\nb\tc", 5);
	printf("  control:      %s\n", read_out());
	if (strcmp(read_out(), "\"a\\nb\\tc\"") != 0) failures++;

	/* 5. 非法 UTF-8 起始字节 0xFF */
	{ char bad[] = { (char)0xFF }; capture(bad, 1); }
	printf("  invalid utf8: %s\n", read_out());
	if (strcmp(read_out(), "\"\\u00ff\"") != 0) failures++;

	/* 6. 合法 UTF-8（中文"中" = E4 B8 AD）*/
	{ char zh[] = { (char)0xE4, (char)0xB8, (char)0xAD }; capture(zh, 3); }
	printf("  valid utf8:   %s\n", read_out());
	if (strcmp(read_out(), "\"\xE4\xB8\xAD\"") != 0) failures++;

	/* 7. 代理区码点 U+D800 (ED A0 80) 必须被拒绝并转义 */
	{ char sur[] = { (char)0xED, (char)0xA0, (char)0x80 }; capture(sur, 3); }
	printf("  surrogate:    %s\n", read_out());
	if (strncmp(read_out(), "\"\\u00ed", 7) != 0) failures++;

	/* 8. 空串 */
	capture("", 0);
	printf("  empty:        %s\n", read_out());
	if (strcmp(read_out(), "\"\"") != 0) failures++;

	/* 9. FILE* 重构必须逐字节等价（全部 256 个单字节 + 多字节序列） */
	{
		int mismatches = fprint_matches_print();

		printf("  fprint==print: %s\n", mismatches ? "MISMATCH" : "ok");
		failures += mismatches;
	}

	printf("\n%s (%d 项失败)\n", failures ? "FAIL" : "PASS", failures);
	return failures ? 1 : 0;
}
