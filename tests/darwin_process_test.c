/* Real macOS child spawning and same-PID restart; authored executables/sockets only. */
#include "darwin_process.h"
#include "hos_relaunch.h"
#include <windows.h>
#include <errno.h>
#include <fcntl.h>
#include <libproc.h>
#include <limits.h>
#include <pthread.h>
#include <signal.h>
#include <stdarg.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL process line=%d: %s errno=%d\n", __LINE__, #x, errno); exit(1); } } while (0)
void hos_log(const char *fmt, ...) { va_list ap; va_start(ap, fmt); vfprintf(stderr, fmt, ap); va_end(ap); fputc('\n', stderr); }
void hos_log_flush(void) { fflush(stderr); }
static const char input[] = "native\0pipe\n";
static _Atomic int churn_ready, churn_stop;
static void *socket_churn(void *p) {
    (void)p;
    int descriptors[64];
    for (int i = 0; i < 64; i++) descriptors[i] = -1;
    unsigned next = 0;
    while (!atomic_load(&churn_stop)) {
        const int fd = socket(AF_UNIX, SOCK_STREAM, 0); /* intentionally no CLOEXEC; never connects anywhere */
        if (fd < 0) continue;
        const unsigned i = next++ % 64;
        if (descriptors[i] >= 0) close(descriptors[i]);
        descriptors[i] = fd;
        if (next >= 64) atomic_store(&churn_ready, 1);
    }
    for (int i = 0; i < 64; i++) if (descriptors[i] >= 0) close(descriptors[i]);
    return NULL;
}
static pthread_t churn_start(void) {
    pthread_t t;
    CHECK(pthread_create(&t, NULL, socket_churn, NULL) == 0);
    for (int i = 0; i < 5000 && !atomic_load(&churn_ready); i++) usleep(1000);
    CHECK(atomic_load(&churn_ready));
    return t;
}
static int extra_descriptors(void) {
    struct proc_fdinfo fds[4096];
    const int bytes = proc_pidinfo(getpid(), PROC_PIDLISTFDS, 0, fds, sizeof fds);
    CHECK(bytes > 0 && bytes < (int)sizeof fds && bytes % (int)sizeof fds[0] == 0);
    int extra = 0;
    for (int i = 0; i < bytes / (int)sizeof fds[0]; i++) if (fds[i].proc_fd > 2) extra++;
    return extra;
}
static int successful(pid_t pid) {
    int status;
    CHECK(darwin_wait_child(pid, &status) == 0);
    return WIFEXITED(status) && WEXITSTATUS(status) == 0;
}
static void send_input(int fd) {
    size_t at = 0;
    while (at < sizeof input) {
        const ssize_t n = write(fd, input + at, sizeof input - at);
        if (n < 0 && errno == EINTR) continue;
        CHECK(n > 0);
        at += (size_t)n;
    }
}
static int consume(void) {
    char bytes[sizeof input + 1];
    size_t at = 0;
    while (at < sizeof bytes) {
        const ssize_t n = read(0, bytes + at, sizeof bytes - at);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) break;
        at += (size_t)n;
    }
    return at == sizeof input && !memcmp(bytes, input, sizeof input) && extra_descriptors() == 0 ? 0 : 9;
}
static uint64_t strings_hash(char *const vector[]) {
    uint64_t hash = UINT64_C(1469598103934665603);
    for (size_t i = 0; vector[i]; i++) {
        const unsigned char *s = (const unsigned char *)vector[i];
        do { hash = (hash ^ *s) * UINT64_C(1099511628211); } while (*s++);
    }
    return hash;
}
typedef struct { pid_t pid; uint64_t argv, env; } RestartReceipt;
static int restart(int argc, char **argv) {
    CHECK(hos_relaunch_keep_startup(argc, argv, environ) == 0);
    CHECK(argc == 6);
    const char *marker = argv[2];
    if (access(marker, F_OK) != 0) {
        int fd = open(marker, O_WRONLY | O_CREAT | O_EXCL, 0600);
        CHECK(fd >= 0);
        const RestartReceipt original = {getpid(), strings_hash(argv), strings_hash(environ)};
        CHECK(write(fd, &original, sizeof original) == sizeof original);
        /* Leave this descriptor open too. The restart must close it without a descriptor snapshot. */
        CHECK(setenv("DARWIN_ORIGINAL", "mutated", 1) == 0);
        CHECK(unsetenv("DARWIN_START_ONLY") == 0);
        CHECK(setenv("DARWIN_NEW_VALUE", "must not survive", 1) == 0);
        argv[0][0] = 'X';
        argv[4][0] = 'X';
        (void)churn_start();
        const int e = hos_relaunch_exec_startup();
        fprintf(stderr, "restart unexpectedly returned %d\n", e);
        return 10;
    }
    CHECK(extra_descriptors() == 0);  /* before opening even the receipt */
    CHECK(!strcmp(argv[0], "DarwinHost original argv0"));
    CHECK(!strcmp(argv[3], "") && !strcmp(argv[4], "quoted space") && !strcmp(argv[5], "Grüße"));
    CHECK(getenv("DARWIN_ORIGINAL") && !strcmp(getenv("DARWIN_ORIGINAL"), "original"));
    CHECK(getenv("DARWIN_START_ONLY") && !strcmp(getenv("DARWIN_START_ONLY"), "retained"));
    CHECK(!getenv("DARWIN_NEW_VALUE"));
    const int fd = open(marker, O_RDONLY | O_CLOEXEC);
    CHECK(fd >= 0);
    RestartReceipt original = {0};
    CHECK(read(fd, &original, sizeof original) == sizeof original);
    close(fd);
    CHECK(original.pid == getpid());
    CHECK(original.argv == strings_hash(argv) && original.env == strings_hash(environ));
    printf("restart same_pid=1 original_argv=6 original_env=retained extra_fds=0 pid=%d\n", getpid());
    return 0;
}
static int remap(const char *exe) {
    CHECK(close(0) == 0 && close(1) == 0);
    int fds[2];
    CHECK(darwin_pipe_cloexec(fds) == 0 && fds[0] == 0 && fds[1] == 1);
    int nullfd = open("/dev/null", O_RDWR | O_CLOEXEC);
    CHECK(nullfd >= 0);
    int standard[3] = {fds[0], nullfd, 2};
    char *args[] = {(char *)exe, "--consume", NULL};
    pid_t pid;
    CHECK(darwin_spawn(&pid, exe, args, environ, standard, 0) == 0);
    close(fds[0]);
    send_input(fds[1]);
    close(fds[1]);
    close(nullfd);
    CHECK(successful(pid));
    return 0;
}
static void test(const char *dir, const char *exe) {
    CHECK(setenv("DARWIN_ORIGINAL", "original", 1) == 0 && setenv("DARWIN_START_ONLY", "retained", 1) == 0);
    char marker[PATH_MAX];
    CHECK(snprintf(marker, sizeof marker, "%s/restart.marker", dir) < (int)sizeof marker);
    char *restart_args[] = {"DarwinHost original argv0", "--restart", marker, "", "quoted space", "Grüße", NULL};
    pid_t pid;
    CHECK(darwin_spawn(&pid, exe, restart_args, environ, NULL, 0) == 0 && successful(pid));
    CHECK(unlink(marker) == 0);
    pthread_t churn = churn_start();
    char *check_args[] = {(char *)exe, "--fds", NULL};
    for (int i = 0; i < 32; i++) {
        CHECK(darwin_spawn(&pid, exe, check_args, environ, NULL, 0) == 0);
        CHECK(successful(pid));
    }
    atomic_store(&churn_stop, 1);
    CHECK(pthread_join(churn, NULL) == 0);
    char *remap_args[] = {(char *)exe, "--remap", NULL};
    CHECK(darwin_spawn(&pid, exe, remap_args, environ, NULL, 0) == 0 && successful(pid));
    int fds[2];
    CHECK(darwin_pipe_cloexec(fds) == 0);
    CHECK((fcntl(fds[0], F_GETFD) & FD_CLOEXEC) && (fcntl(fds[1], F_GETFD) & FD_CLOEXEC));
    CHECK(fcntl(fds[1], F_GETNOSIGPIPE) == 1);
    int standard[3] = {fds[0], 1, 2};
    char *failure_args[] = {(char *)exe, "--fail", NULL};
    CHECK(darwin_spawn(&pid, exe, failure_args, environ, standard, 0) == 0);
    close(fds[0]);
    char buffer[65536] = {0};
    ssize_t n;
    do { n = write(fds[1], buffer, sizeof buffer); } while (n > 0 || (n < 0 && errno == EINTR));
    CHECK(n < 0 && errno == EPIPE);  /* no SIGPIPE disposition change and no crash */
    close(fds[1]);
    int status;
    CHECK(darwin_wait_child(pid, &status) == 0 && WIFEXITED(status) && WEXITSTATUS(status) == 7);
    char *missing[] = {"/no/such/DarwinHost-child", NULL};
    CHECK(darwin_spawn(&pid, missing[0], missing, environ, NULL, 0) == ENOENT && pid == 0);
    char *exit_args[] = {(char *)exe, "--exit", NULL};
    CHECK(darwin_spawn(&pid, exe, exit_args, environ, NULL, 0) == 0);
    CHECK(darwin_reap_child(pid) == 0);
    for (int i = 0; i < 2000 && kill(pid, 0) == 0; i++) usleep(1000);
    errno = 0;
    CHECK(waitpid(pid, &status, WNOHANG) == -1 && errno == ECHILD);
    puts("process spawn_closure=32/32 concurrent_unflagged_sockets=1 closed_stdio_remap=1 pipe_cloexec=1 epipe_survived=1 exit7_reaped=1 missing_child=ENOENT detached_reaped=1");
}
int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IONBF, 0);
    char exe[PATH_MAX];
    CHECK(darwin_executable_path(exe, sizeof exe) == 0);
    if (argc > 1 && !strcmp(argv[1], "--restart")) return restart(argc, argv);
    if (argc > 1 && !strcmp(argv[1], "--fds")) return extra_descriptors() != 0;
    if (argc > 1 && !strcmp(argv[1], "--consume")) return consume();
    if (argc > 1 && !strcmp(argv[1], "--fail")) return 7;
    if (argc > 1 && !strcmp(argv[1], "--exit")) return 0;
    if (argc > 1 && !strcmp(argv[1], "--remap")) return remap(exe);
    CHECK(argc == 3 && !strcmp(argv[1], "--test"));
    test(argv[2], exe);
    puts("darwin_process PASS");
    return 0;
}
