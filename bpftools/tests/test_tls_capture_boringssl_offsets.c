/* SPDX-License-Identifier: (LGPL-2.1 OR BSD-2-Clause) */
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <fcntl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>

static off_t fail_read_offset = -1;
static int fail_read_nth;
static int matching_reads;

static ssize_t checked_pread(int fd, void *buf, size_t count, off_t offset)
{
    if (offset == fail_read_offset && ++matching_reads == fail_read_nth) {
        errno = EIO;
        return -1;
    }
    return pread(fd, buf, count, offset);
}

/*
 * Pull the real static implementation into this translation unit so we can
 * exercise the exact current BoringSSL offset detection logic without editing
 * production code.
 */
#define main tls_capture_main_unused
#define pread checked_pread
#include "../tls_capture/tls_capture.c"
#undef pread
#undef main

/*
 * Synthetic fixtures need the exact prologue bytes. Keep these in sync with
 * the patterns inside find_boringssl_offsets(); the real-binary test below
 * also guards the production copies.
 */
static const unsigned char hs_pat[] = {
    0x55, 0x48, 0x89, 0xe5, 0x41, 0x57, 0x41, 0x56,
    0x41, 0x55, 0x41, 0x54, 0x53, 0x48, 0x83, 0xec,
    0x28, 0x49, 0x89, 0xfc, 0x48, 0x8b, 0x47, 0x30
};
static const unsigned char read_pat[] = {
    0x55, 0x48, 0x89, 0xe5, 0x41, 0x57, 0x41, 0x56,
    0x53, 0x50, 0x48, 0x83, 0xbf, 0x98, 0x00, 0x00,
    0x00, 0x00, 0x74
};
static const unsigned char write_pat[] = {
    0x55, 0x48, 0x89, 0xe5, 0x41, 0x57, 0x41, 0x56,
    0x41, 0x55, 0x41, 0x54, 0x53, 0x48, 0x83, 0xec,
    0x18, 0x41, 0x89, 0xd7, 0x49, 0x89, 0xf6, 0x48,
    0x89, 0xfb
};

#define TEST_CHUNK_SIZE BORINGSSL_SCAN_CHUNK /* place read across a chunk edge */
#define HS_DELTA 0x6F0u
#define WR_DELTA 0xCA0u
#define WR_FALLBACK_DELTA 0x1234u

static int tests_failed = 0;

static void check(bool condition, const char *message)
{
    if (!condition) {
        fprintf(stderr, "ASSERTION FAILED: %s\n", message);
        tests_failed++;
    }
}

static void assert_true(bool condition, const char *message)
{
    check(condition, message);
}

static int write_at(int fd, size_t offset, const unsigned char *data, size_t len)
{
    while (len > 0) {
        ssize_t n = pwrite(fd, data, len, (off_t)offset);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            return -1;
        }
        if (n == 0)
            return -1;
        data += n;
        offset += (size_t)n;
        len -= (size_t)n;
    }
    return 0;
}

struct placement {
    size_t offset;
    const unsigned char *data;
    size_t len;
};

/* Create a sparse temp file, extend it to file_size, then place fixtures. */
static int make_fixture(char *path, size_t path_size, size_t file_size,
                        const struct placement *placements, size_t n)
{
    const char *dir = getenv("TMPDIR");
    int fd;

    if (!dir || dir[0] == '\0')
        dir = "/tmp";
    if (snprintf(path, path_size, "%s/mtagentlens_bs_XXXXXX", dir) >=
        (int)path_size)
        return -1;

    fd = mkstemp(path);
    if (fd < 0)
        return -1;
    if (ftruncate(fd, (off_t)file_size) != 0)
        goto fail;

    for (size_t i = 0; i < n; i++) {
        if (placements[i].offset + placements[i].len > file_size)
            goto fail;
        if (write_at(fd, placements[i].offset, placements[i].data,
                     placements[i].len) != 0)
            goto fail;
    }

    close(fd);
    return 0;
fail:
    close(fd);
    unlink(path);
    return -1;
}

static void expect_offsets(const char *name, const char *path, bool found,
                           size_t hs_off, size_t read_off, size_t write_off)
{
    struct boringssl_offsets o = find_boringssl_offsets(path);
    char msg[256];

    printf("[%s] found=%d hs=0x%zx read=0x%zx write=0x%zx\n", name,
           o.found ? 1 : 0, o.ssl_do_handshake, o.ssl_read, o.ssl_write);

    snprintf(msg, sizeof(msg), "%s: found == %s", name,
             found ? "true" : "false");
    check(o.found == found, msg);
    snprintf(msg, sizeof(msg), "%s: SSL_read offset", name);
    check(o.ssl_read == read_off, msg);
    snprintf(msg, sizeof(msg), "%s: SSL_write offset", name);
    check(o.ssl_write == write_off, msg);
    snprintf(msg, sizeof(msg), "%s: SSL_do_handshake offset", name);
    check(o.ssl_do_handshake == hs_off, msg);
}

static void test_boundary_straddling(void)
{
    char path[256];
    size_t read_off = TEST_CHUNK_SIZE - 7; /* read pattern crosses chunk edge */
    size_t hs_off = read_off - HS_DELTA;
    size_t wr_off = read_off + WR_DELTA;
    size_t size = wr_off + sizeof(write_pat) + 16;
    struct placement p[] = {
        {hs_off, hs_pat, sizeof(hs_pat)},
        {read_off, read_pat, sizeof(read_pat)},
        {wr_off, write_pat, sizeof(write_pat)},
    };

    if (make_fixture(path, sizeof(path), size, p, 3) != 0) {
        check(false, "boundary fixture: failed to create temp file");
        return;
    }
    expect_offsets("boundary", path, true, hs_off, read_off, wr_off);
    unlink(path);
}

static void test_optional_handshake_missing(void)
{
    char path[256];
    size_t read_off = 4096;
    size_t wr_off = read_off + WR_DELTA;
    size_t size = wr_off + sizeof(write_pat) + 32;
    struct placement p[] = {
        {read_off, read_pat, sizeof(read_pat)},
        {wr_off, write_pat, sizeof(write_pat)},
    };

    if (make_fixture(path, sizeof(path), size, p, 2) != 0) {
        check(false, "no-handshake fixture: failed to create temp file");
        return;
    }
    expect_offsets("no-handshake", path, true, 0, read_off, wr_off);
    unlink(path);
}

static void test_handshake_fallback_search(void)
{
    char path[256];
    size_t read_off = 4096;
    size_t wr_off = read_off + WR_DELTA;
    size_t hs_off = 65536; /* not at read - delta; found by full scan */
    size_t size = hs_off + sizeof(hs_pat) + 16;
    struct placement p[] = {
        {read_off, read_pat, sizeof(read_pat)},
        {wr_off, write_pat, sizeof(write_pat)},
        {hs_off, hs_pat, sizeof(hs_pat)},
    };

    if (make_fixture(path, sizeof(path), size, p, 3) != 0) {
        check(false, "handshake-fallback fixture: failed to create temp file");
        return;
    }
    expect_offsets("handshake-fallback", path, true, hs_off, read_off, wr_off);
    unlink(path);
}

static void test_write_fallback_window(void)
{
    char path[256];
    size_t read_off = 4096;
    size_t wr_off = read_off + WR_FALLBACK_DELTA;
    size_t size = wr_off + sizeof(write_pat) + 8;
    struct placement p[] = {
        {read_off, read_pat, sizeof(read_pat)},
        {wr_off, write_pat, sizeof(write_pat)},
    };

    if (make_fixture(path, sizeof(path), size, p, 2) != 0) {
        check(false, "write-fallback fixture: failed to create temp file");
        return;
    }
    expect_offsets("write-fallback", path, true, 0, read_off, wr_off);
    unlink(path);
}

static void test_missing_write_keeps_read(void)
{
    char path[256];
    size_t read_off = 4096;
    size_t size = read_off + sizeof(read_pat) + 0x20000;
    struct placement p[] = {
        {read_off, read_pat, sizeof(read_pat)},
    };

    if (make_fixture(path, sizeof(path), size, p, 1) != 0) {
        check(false, "missing-write fixture: failed to create temp file");
        return;
    }
    /* Old logic reports found=false but still fills ssl_read. */
    expect_offsets("missing-write", path, false, 0, read_off, 0);
    unlink(path);
}

static void test_read_errors_are_not_optional_handshake_absence(void)
{
    char path[256];
    struct placement p[] = {
        {4096, read_pat, sizeof(read_pat)},
        {4096 + WR_DELTA, write_pat, sizeof(write_pat)},
    };
    if (make_fixture(path, sizeof(path), 16384, p, 2) != 0) {
        check(false, "IO-error fixture creation");
        return;
    }
    const off_t offsets[] = {0, 4096 - HS_DELTA, 4096 + WR_DELTA};
    const int nth[] = {2, 1, 1};
    for (size_t i = 0; i < 3; i++) {
        fail_read_offset = offsets[i];
        fail_read_nth = nth[i];
        matching_reads = 0;
        struct boringssl_offsets result = find_boringssl_offsets(path);
        check(!result.found, "read failure must not become successful detection");
    }
    fail_read_offset = -1;
    unlink(path);
}

static void test_truncated_and_empty(void)
{
    char path[256];
    size_t read_off = 4096;
    struct placement p[] = {
        {read_off, read_pat, 10}, /* partial prologue, no full match */
    };

    if (make_fixture(path, sizeof(path), read_off + 10, p, 1) != 0) {
        check(false, "truncated fixture: failed to create temp file");
    } else {
        expect_offsets("truncated-pattern", path, false, 0, 0, 0);
        unlink(path);
    }

    if (make_fixture(path, sizeof(path), 0, NULL, 0) != 0) {
        check(false, "empty fixture: failed to create temp file");
    } else {
        expect_offsets("empty-file", path, false, 0, 0, 0);
        unlink(path);
    }
}

/*
 * Fork a child with a hard address-space cap smaller than the file. The old
 * whole-file malloc cannot fit; bounded chunk scanning can. The parent keeps
 * its normal limits.
 */
static void test_large_sparse_bounded_scan(void)
{
    enum { SPARSE_SIZE = 256 * 1024 * 1024 };
    char path[256];
    size_t read_off = (size_t)SPARSE_SIZE - 0x20000;
    size_t hs_off = read_off - HS_DELTA;
    size_t wr_off = read_off + WR_DELTA;
    struct placement p[] = {
        {hs_off, hs_pat, sizeof(hs_pat)},
        {read_off, read_pat, sizeof(read_pat)},
        {wr_off, write_pat, sizeof(write_pat)},
    };
    pid_t pid;
    int status = 0;

    if (make_fixture(path, sizeof(path), SPARSE_SIZE, p, 3) != 0) {
        check(false, "sparse fixture: failed to create temp file");
        return;
    }

    pid = fork();
    if (pid < 0) {
        check(false, "sparse fixture: fork failed");
        unlink(path);
        return;
    }
    if (pid == 0) {
        struct rlimit lim = { 128 * 1024 * 1024, 128 * 1024 * 1024 };
        void *probe;
        struct boringssl_offsets o;

        if (setrlimit(RLIMIT_AS, &lim) != 0)
            _exit(3);
        /* Prove the cap really rejects a whole-file allocation. */
        probe = malloc((size_t)SPARSE_SIZE);
        if (probe != NULL)
            _exit(4);
        o = find_boringssl_offsets(path);
        if (!o.found)
            _exit(5);
        if (o.ssl_read != read_off || o.ssl_write != wr_off ||
            o.ssl_do_handshake != hs_off)
            _exit(6);
        _exit(0);
    }

    if (waitpid(pid, &status, 0) < 0) {
        check(false, "sparse fixture: waitpid failed");
        unlink(path);
        return;
    }
    if (!WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        fprintf(stderr,
                "sparse child status=%d (3=rlimit, 4=probe alloc, 5=not found, 6=bad offsets)\n",
                WIFEXITED(status) ? WEXITSTATUS(status) : -1);
        check(false, "sparse fixture: bounded scan failed under 128MiB RLIMIT_AS");
    } else {
        printf("[sparse-128MiB] bounded scan passed\n");
    }
    unlink(path);
}

static void run_synthetic_tests(void)
{
    test_boundary_straddling();
    test_optional_handshake_missing();
    test_handshake_fallback_search();
    test_write_fallback_window();
    test_missing_write_keeps_read();
    test_read_errors_are_not_optional_handshake_absence();
    test_truncated_and_empty();
    test_large_sparse_bounded_scan();
}

int main(void)
{
    run_synthetic_tests();
    if (tests_failed != 0) {
        fprintf(stderr,
                "Synthetic tests failed with %d assertion failure(s).\n",
                tests_failed);
        return 1;
    }

    const char *binary_path = getenv("MTAGENTLENS_CLAUDE_BINARY");
    if (!binary_path || binary_path[0] == '\0') {
        fprintf(stderr,
                "SKIP: set MTAGENTLENS_CLAUDE_BINARY to a readable Claude ELF\n");
        return 0;
    }
    if (access(binary_path, R_OK) != 0) {
        fprintf(stderr,
                "SKIP: MTAGENTLENS_CLAUDE_BINARY is not readable: %s\n",
                binary_path);
        return 0;
    }

    struct boringssl_offsets offsets = find_boringssl_offsets(binary_path);

    fprintf(stderr,
            "find_boringssl_offsets(%s) => found=%s, handshake=0x%zx, read=0x%zx, write=0x%zx\n",
            binary_path,
            offsets.found ? "true" : "false",
            offsets.ssl_do_handshake,
            offsets.ssl_read,
            offsets.ssl_write);

    assert_true(offsets.found,
                "expected current Claude ELF BoringSSL offset detection to succeed");
    assert_true(offsets.ssl_read > 0,
                "expected SSL_read offset to be non-zero");
    assert_true(offsets.ssl_write > 0,
                "expected SSL_write offset to be non-zero");
    /* SSL_do_handshake is optional: Bun >= v1.4.x changed its prologue, so
     * detection may legitimately return 0 here (handshake events degrade,
     * data capture via SSL_read/SSL_write is unaffected). */
    if (offsets.ssl_do_handshake == 0) {
        fprintf(stderr,
                "note: SSL_do_handshake offset is 0 (Bun >= v1.4 degraded); data capture unaffected\n");
    }

    if (tests_failed != 0) {
        fprintf(stderr, "Test failed with %d assertion failure(s).\n", tests_failed);
        return 1;
    }

    fprintf(stderr, "Test passed.\n");
    return 0;
}
