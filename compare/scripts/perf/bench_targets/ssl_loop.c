/*
 * ssl_loop.c - local OpenSSL SSL_read/SSL_write microbenchmark.
 *
 * A real TLS 1.2 handshake is completed between a client and a server SSL
 * object connected by two in-process memory BIO pairs. Anonymous TLS is used
 * only on these local memory BIOs: there is no socket, certificate, key file,
 * or network traffic. The payload is a fixed 256-byte buffer echoed in both
 * directions so every iteration performs one successful write and read on
 * each endpoint, and the payload is compared at both receivers.
 *
 * Usage: ./ssl_loop [iterations] [--warmup N] [--gate]
 *   iterations   measured read/write loop iterations (default 200000)
 *   --warmup N   successful echo iterations before the measured loop
 *   --gate       after handshake/warmup print one JSON readiness line to
 *                stdout, then wait for a line "GO" on stdin before timing
 *
 * The final stdout line is a JSON object with successful read/write counts,
 * byte counts, handshake status, and elapsed_ns for the measured loop only.
 * Any invalid argument or failed SSL call exits nonzero.
 */

#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <openssl/err.h>
#include <openssl/ssl.h>

#define DEFAULT_ITERATIONS 200000
#define PAYLOAD_BYTES 256
#define BIO_PAIR_BYTES (1 << 16)
#define MAX_PUMP_STEPS 1000

struct counters {
    unsigned long long writes;
    unsigned long long reads;
    unsigned long long bytes_written;
    unsigned long long bytes_read;
};

static int usage(const char *message) {
    fprintf(stderr, "ssl_loop: %s\n", message);
    fprintf(stderr, "usage: ssl_loop [iterations] [--warmup N] [--gate]\n");
    return 2;
}

static int parse_nonnegative(const char *text, const char *name, int *out) {
    char *end = NULL;
    long value;

    errno = 0;
    value = strtol(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || value < 0 || value > INT_MAX) {
        fprintf(stderr, "ssl_loop: invalid %s: %s\n", name, text);
        return -1;
    }
    *out = (int)value;
    return 0;
}

static int ssl_failure(const char *what, int ret) {
    unsigned long error = ERR_get_error();

    fprintf(stderr, "ssl_loop: %s failed (ret=%d)", what, ret);
    if (error != 0) {
        char buffer[256];
        ERR_error_string_n(error, buffer, sizeof(buffer));
        fprintf(stderr, ": %s", buffer);
    }
    fputc('\n', stderr);
    return 1;
}

/* Drive one SSL object until it either finishes or needs its peer. */
static int drive_handshake(SSL *ssl) {
    int step;

    for (step = 0; step < MAX_PUMP_STEPS; step++) {
        int ret = SSL_do_handshake(ssl);
        int error;

        if (ret == 1) {
            return 1;
        }
        error = SSL_get_error(ssl, ret);
        if (error == SSL_ERROR_WANT_READ || error == SSL_ERROR_WANT_WRITE) {
            return 0;
        }
        return -1;
    }
    return -1;
}

static int complete_handshake(SSL *client, SSL *server) {
    int step;

    for (step = 0; step < MAX_PUMP_STEPS; step++) {
        int client_state = drive_handshake(client);
        int server_state = drive_handshake(server);

        if (client_state < 0 || server_state < 0) {
            return -1;
        }
        if (client_state == 1 && server_state == 1) {
            return 0;
        }
    }
    return -1;
}

static int echo_once(SSL *client, SSL *server, const unsigned char *payload,
                     unsigned char *buffer, struct counters *client_counters,
                     struct counters *server_counters) {
    int ret;

    ret = SSL_write(client, payload, PAYLOAD_BYTES);
    if (ret != PAYLOAD_BYTES) {
        return ssl_failure("SSL_write(client)", ret);
    }
    client_counters->writes++;
    client_counters->bytes_written += PAYLOAD_BYTES;

    ret = SSL_read(server, buffer, PAYLOAD_BYTES);
    if (ret != PAYLOAD_BYTES) {
        return ssl_failure("SSL_read(server)", ret);
    }
    server_counters->reads++;
    server_counters->bytes_read += PAYLOAD_BYTES;
    if (memcmp(payload, buffer, PAYLOAD_BYTES) != 0) {
        fprintf(stderr, "ssl_loop: server payload mismatch\n");
        return 1;
    }

    ret = SSL_write(server, buffer, PAYLOAD_BYTES);
    if (ret != PAYLOAD_BYTES) {
        return ssl_failure("SSL_write(server)", ret);
    }
    server_counters->writes++;
    server_counters->bytes_written += PAYLOAD_BYTES;

    ret = SSL_read(client, buffer, PAYLOAD_BYTES);
    if (ret != PAYLOAD_BYTES) {
        return ssl_failure("SSL_read(client)", ret);
    }
    client_counters->reads++;
    client_counters->bytes_read += PAYLOAD_BYTES;
    if (memcmp(payload, buffer, PAYLOAD_BYTES) != 0) {
        fprintf(stderr, "ssl_loop: client payload mismatch\n");
        return 1;
    }
    return 0;
}

int main(int argc, char **argv) {
    int iterations = DEFAULT_ITERATIONS;
    int warmup = 0;
    int gate = 0;
    int positional_seen = 0;
    int index;
    int rc = 1;
    SSL_CTX *client_ctx = NULL;
    SSL_CTX *server_ctx = NULL;
    SSL *client = NULL;
    SSL *server = NULL;
    BIO *client_rbio = NULL;
    BIO *client_wbio = NULL;
    BIO *server_rbio = NULL;
    BIO *server_wbio = NULL;
    unsigned char payload[PAYLOAD_BYTES];
    unsigned char buffer[PAYLOAD_BYTES];
    struct counters warmup_client = {0, 0, 0, 0};
    struct counters warmup_server = {0, 0, 0, 0};
    struct counters client_counters = {0, 0, 0, 0};
    struct counters server_counters = {0, 0, 0, 0};
    struct timespec started;
    struct timespec finished;
    long long elapsed_ns;

    for (index = 1; index < argc; index++) {
        if (strcmp(argv[index], "--warmup") == 0) {
            if (index + 1 >= argc) {
                return usage("--warmup requires a value");
            }
            if (parse_nonnegative(argv[++index], "--warmup", &warmup) != 0) {
                return 2;
            }
        } else if (strcmp(argv[index], "--gate") == 0) {
            gate = 1;
        } else if (strcmp(argv[index], "--help") == 0 || strcmp(argv[index], "-h") == 0) {
            printf("usage: ssl_loop [iterations] [--warmup N] [--gate]\n");
            return 0;
        } else if (argv[index][0] == '-' && argv[index][1] != '\0') {
            return usage("unknown option");
        } else {
            if (positional_seen != 0) {
                return usage("too many positional arguments");
            }
            positional_seen = 1;
            if (parse_nonnegative(argv[index], "iterations", &iterations) != 0) {
                return 2;
            }
        }
    }
    if (iterations < 1) {
        return usage("iterations must be at least 1");
    }

    memset(payload, 'A', sizeof(payload));
    memset(buffer, 0, sizeof(buffer));

    SSL_library_init();
    SSL_load_error_strings();

    client_ctx = SSL_CTX_new(TLS_client_method());
    server_ctx = SSL_CTX_new(TLS_server_method());
    if (client_ctx == NULL || server_ctx == NULL) {
        ssl_failure("SSL_CTX_new", 0);
        goto cleanup;
    }

    /*
     * Anonymous TLS 1.2 is deliberate and local-only. Security level 0 is
     * required because anonymous cipher suites are disabled by default.
     */
    if (SSL_CTX_set_min_proto_version(client_ctx, TLS1_2_VERSION) != 1 ||
        SSL_CTX_set_max_proto_version(client_ctx, TLS1_2_VERSION) != 1 ||
        SSL_CTX_set_min_proto_version(server_ctx, TLS1_2_VERSION) != 1 ||
        SSL_CTX_set_max_proto_version(server_ctx, TLS1_2_VERSION) != 1) {
        ssl_failure("TLS version setup", 0);
        goto cleanup;
    }
    if (SSL_CTX_set_cipher_list(client_ctx, "ADH-AES256-GCM-SHA384") != 1 ||
        SSL_CTX_set_cipher_list(server_ctx, "ADH-AES256-GCM-SHA384") != 1) {
        ssl_failure("anonymous cipher setup", 0);
        goto cleanup;
    }
    SSL_CTX_set_security_level(client_ctx, 0);
    SSL_CTX_set_security_level(server_ctx, 0);
    SSL_CTX_set_dh_auto(server_ctx, 1);
    SSL_CTX_set_verify(client_ctx, SSL_VERIFY_NONE, NULL);

    if (BIO_new_bio_pair(&client_wbio, BIO_PAIR_BYTES, &server_rbio, BIO_PAIR_BYTES) != 1 ||
        BIO_new_bio_pair(&server_wbio, BIO_PAIR_BYTES, &client_rbio, BIO_PAIR_BYTES) != 1) {
        ssl_failure("BIO_new_bio_pair", 0);
        goto cleanup;
    }

    client = SSL_new(client_ctx);
    server = SSL_new(server_ctx);
    if (client == NULL || server == NULL) {
        ssl_failure("SSL_new", 0);
        goto cleanup;
    }
    SSL_set_bio(client, client_rbio, client_wbio);
    client_rbio = NULL;
    client_wbio = NULL;
    SSL_set_bio(server, server_rbio, server_wbio);
    server_rbio = NULL;
    server_wbio = NULL;
    SSL_set_connect_state(client);
    SSL_set_accept_state(server);

    if (complete_handshake(client, server) != 0) {
        ssl_failure("TLS handshake", 0);
        goto cleanup;
    }

    for (index = 0; index < warmup; index++) {
        if (echo_once(client, server, payload, buffer, &warmup_client,
                      &warmup_server) != 0) {
            goto cleanup;
        }
    }

    if (gate != 0) {
        char line[64];
        size_t length;

        printf("{\"schema\":\"mtagentlens.ssl-loop/1\",\"ready\":true,"
               "\"handshake\":\"ok\",\"pid\":%ld,\"payload_bytes\":%d,"
               "\"warmup_iterations\":%d}\n",
               (long)getpid(), PAYLOAD_BYTES, warmup);
        fflush(stdout);

        if (fgets(line, sizeof(line), stdin) == NULL) {
            fprintf(stderr, "ssl_loop: readiness gate received no GO\n");
            goto cleanup;
        }
        length = strlen(line);
        while (length > 0 &&
               (line[length - 1] == '\n' || line[length - 1] == '\r' ||
                line[length - 1] == ' ' || line[length - 1] == '\t')) {
            line[--length] = '\0';
        }
        if (strcmp(line, "GO") != 0) {
            fprintf(stderr, "ssl_loop: readiness gate expected GO\n");
            goto cleanup;
        }
    }

    if (clock_gettime(CLOCK_MONOTONIC, &started) != 0) {
        fprintf(stderr, "ssl_loop: clock_gettime(start) failed: %s\n", strerror(errno));
        goto cleanup;
    }
    for (index = 0; index < iterations; index++) {
        if (echo_once(client, server, payload, buffer, &client_counters,
                      &server_counters) != 0) {
            goto cleanup;
        }
    }
    if (clock_gettime(CLOCK_MONOTONIC, &finished) != 0) {
        fprintf(stderr, "ssl_loop: clock_gettime(end) failed: %s\n", strerror(errno));
        goto cleanup;
    }
    elapsed_ns = (long long)(finished.tv_sec - started.tv_sec) * 1000000000LL +
                 (long long)(finished.tv_nsec - started.tv_nsec);

    printf("{\"schema\":\"mtagentlens.ssl-loop/1\",\"status\":\"ok\","
           "\"handshake\":\"ok\",\"iterations\":%d,\"warmup_iterations\":%d,"
           "\"payload_bytes\":%d,"
           "\"client\":{\"write_count\":%llu,\"read_count\":%llu,"
           "\"bytes_written\":%llu,\"bytes_read\":%llu},"
           "\"server\":{\"write_count\":%llu,\"read_count\":%llu,"
           "\"bytes_written\":%llu,\"bytes_read\":%llu},"
           "\"ssl_write_count\":%llu,\"ssl_read_count\":%llu,"
           "\"ssl_operation_count\":%llu,"
           "\"bytes_written\":%llu,\"bytes_read\":%llu,\"elapsed_ns\":%lld}\n",
           iterations, warmup, PAYLOAD_BYTES,
           client_counters.writes, client_counters.reads,
           client_counters.bytes_written, client_counters.bytes_read,
           server_counters.writes, server_counters.reads,
           server_counters.bytes_written, server_counters.bytes_read,
           client_counters.writes + server_counters.writes,
           client_counters.reads + server_counters.reads,
           client_counters.writes + server_counters.writes +
               client_counters.reads + server_counters.reads,
           client_counters.bytes_written + server_counters.bytes_written,
           client_counters.bytes_read + server_counters.bytes_read,
           elapsed_ns);
    fflush(stdout);
    rc = 0;

cleanup:
    if (client != NULL) {
        SSL_free(client);
    }
    if (server != NULL) {
        SSL_free(server);
    }
    BIO_free(client_rbio);
    BIO_free(client_wbio);
    BIO_free(server_rbio);
    BIO_free(server_wbio);
    SSL_CTX_free(client_ctx);
    SSL_CTX_free(server_ctx);
    ERR_clear_error();
    return rc;
}
