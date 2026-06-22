/*
 * ssl_loop.c — SSL_read/SSL_write 微基准 (simplified for probe timing)
 *
<<<<<<< Updated upstream
 * 使用 OpenSSL BIO pair (内存 SSL) 在循环中调用 SSL_write/SSL_read,
 * 触发 MtAgentPrism 的 uprobe/SSL_read 和 uprobe/SSL_write 探针。
=======
 * 简单方法: 创建 SSL 对象并反复调用 SSL_read/SSL_write,
 * 即使调用失败, uprobe 仍会被触发。对于 uprobe 延迟测量,
 * 我们只关心探针执行时间, 不关心 SSL 调用是否成功。
>>>>>>> Stashed changes
 *
 * 用法: ./ssl_loop [iterations=200000]
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <openssl/ssl.h>
#include <openssl/err.h>

#define DEFAULT_ITERATIONS 200000
#define BUF_SIZE 256

int main(int argc, char **argv) {
    int iterations = DEFAULT_ITERATIONS;
    if (argc > 1) {
        iterations = atoi(argv[1]);
        if (iterations <= 0) iterations = DEFAULT_ITERATIONS;
    }

    SSL_library_init();
    SSL_load_error_strings();

    SSL_CTX *ctx = SSL_CTX_new(TLS_client_method());
    if (!ctx) return 1;

    SSL *ssl = SSL_new(ctx);
    if (!ssl) return 1;

    /* 不需要真实连接 — uprobe 在任何调用时都会触发 */
    char buf[BUF_SIZE];
    memset(buf, 'A', BUF_SIZE);

    for (int i = 0; i < iterations; i++) {
        /* SSL_write: 每次触发 uprobe/SSL_write */
        SSL_write(ssl, buf, BUF_SIZE);
        /* SSL_read: 每次触发 uprobe/SSL_read */
        SSL_read(ssl, buf, BUF_SIZE);
    }

    SSL_free(ssl);
    SSL_CTX_free(ctx);
    return 0;
}
