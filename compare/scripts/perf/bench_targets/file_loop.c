/*
 * file_loop.c — 文件操作微基准目标程序
 *
 * 在循环中反复打开/写入/关闭/读取文件, 触发 MtAgentLens 的文件访问探针。
 *
 * 用法: ./file_loop [iterations=100000]
 */

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/stat.h>

#define DEFAULT_ITERATIONS 100000
#define TMP_FILE "/tmp/mtagentlens_perf_bench_file"

int main(int argc, char **argv) {
    int iterations = DEFAULT_ITERATIONS;
    if (argc > 1) {
        iterations = atoi(argv[1]);
        if (iterations <= 0) iterations = DEFAULT_ITERATIONS;
    }

    const char *data = "benchmark payload\n";
    int data_len = strlen(data);
    char buf[64];

    for (int i = 0; i < iterations; i++) {
        /* 写入 */
        int fd = open(TMP_FILE, O_CREAT | O_WRONLY | O_TRUNC, 0644);
        if (fd < 0) {
            perror("open for write");
            return 1;
        }
        if (write(fd, data, data_len) < 0) {
            perror("write");
            close(fd);
            return 1;
        }
        close(fd);

        /* 读取 */
        fd = open(TMP_FILE, O_RDONLY);
        if (fd < 0) {
            perror("open for read");
            return 1;
        }
        if (read(fd, buf, sizeof(buf)) < 0) {
            perror("read");
            close(fd);
            return 1;
        }
        close(fd);
    }

    /* 清理 */
    unlink(TMP_FILE);
    return 0;
}
