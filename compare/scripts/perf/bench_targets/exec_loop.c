/*
 * exec_loop.c — fork/exec 微基准目标程序
 *
 * 在循环中 fork 子进程并执行 /bin/true, 触发 MtAgentPrism 的
 * sched_process_fork / sched_process_exec / sched_process_exit tracepoints。
 *
 * 用法: ./exec_loop [iterations=10000]
 */

#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>

#define DEFAULT_ITERATIONS 10000

int main(int argc, char **argv) {
    int iterations = DEFAULT_ITERATIONS;
    if (argc > 1) {
        iterations = atoi(argv[1]);
        if (iterations <= 0) iterations = DEFAULT_ITERATIONS;
    }

    for (int i = 0; i < iterations; i++) {
        pid_t pid = fork();
        if (pid < 0) {
            perror("fork");
            return 1;
        }
        if (pid == 0) {
            /* 子进程 */
            execl("/bin/true", "true", (char *)NULL);
            _exit(127); /* execl 失败时退出 */
        }
        /* 父进程等待子进程 */
        int status;
        waitpid(pid, &status, 0);
    }

    return 0;
}
