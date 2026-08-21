/* SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause) */
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

/*
 * Pull the real static implementation into this translation unit so we can
 * exercise the exact current BoringSSL offset detection logic without editing
 * production code.
 */
#define main tls_capture_main_unused
#include "../tls_capture/tls_capture.c"
#undef main

static int tests_failed = 0;

static void assert_true(bool condition, const char *message)
{
    if (!condition) {
        fprintf(stderr, "ASSERTION FAILED: %s\n", message);
        tests_failed++;
    }
}

int main(void)
{
    const char *binary_path = getenv("MTAGENTLENS_CLAUDE_BINARY");
    if (!binary_path || binary_path[0] == '\0') {
        binary_path = "/home/lzk/.npm-global/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe";
    }
    if (access(binary_path, R_OK) != 0) {
        fprintf(stderr,
                "SKIP: set MTAGENTLENS_CLAUDE_BINARY to a readable Claude ELF (current: %s)\n",
                binary_path);
        return 0;
    }

    struct boringssl_offsets offsets = find_boringssl_offsets(binary_path);

    fprintf(stderr,
            "find_boringssl_offsets(%s) => found=%s, handshake=0x%lx, read=0x%lx, write=0x%lx\n",
            binary_path,
            offsets.found ? "true" : "false",
            offsets.ssl_do_handshake,
            offsets.ssl_read,
            offsets.ssl_write);

    assert_true(offsets.found,
                "expected current Claude ELF BoringSSL offset detection to succeed");
    assert_true(offsets.ssl_do_handshake > 0,
                "expected SSL_do_handshake offset to be non-zero");
    assert_true(offsets.ssl_read > 0,
                "expected SSL_read offset to be non-zero");
    assert_true(offsets.ssl_write > 0,
                "expected SSL_write offset to be non-zero");

    if (tests_failed != 0) {
        fprintf(stderr, "Test failed with %d assertion failure(s).\n", tests_failed);
        return 1;
    }

    fprintf(stderr, "Test passed.\n");
    return 0;
}
