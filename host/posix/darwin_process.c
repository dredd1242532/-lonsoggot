/* SPDX-License-Identifier: GPL-3.0-or-later */
/* macOS 14.4 baseline: public dyld/libproc and the spawn extensions available since 10.7.
   In particular, pipe2 and Linux addclosefrom_np are not used. Setting CLOEXEC on a snapshot of /dev/fd
   is not enough: another thread can open a socket after the snapshot. CLOEXEC_DEFAULT closes that race. */
#if defined(__APPLE__)
#include "darwin_process.h"
#include <errno.h>
#include <fcntl.h>
#include <libproc.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <pthread.h>
#include <signal.h>
#include <spawn.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

int darwin_executable_path(char *out, size_t cap) {
    if (!out || !cap) return EINVAL;
    out[0] = 0;
    char path[PROC_PIDPATHINFO_MAXSIZE];
    if (proc_pidpath(getpid(), path, sizeof path) <= 0) {
        uint32_t n = sizeof path;
        if (_NSGetExecutablePath(path, &n)) return ENAMETOOLONG;
    }
    char resolved[PATH_MAX];
    const char *chosen = realpath(path, resolved) ? resolved : path;
    const size_t n = strlen(chosen);
    if (n >= cap) return ENAMETOOLONG;
    if (chosen[0] != '/') return ENOENT;
    memcpy(out, chosen, n + 1);
    return 0;
}

int darwin_spawn(pid_t *pid, const char *path, char *const argv[], char *const envp[], const int standard[3], int replace) {
    if (!path || !*path || !argv || !argv[0] || !envp || (!replace && !pid) || (replace && standard)) return EINVAL;
    if (pid) *pid = 0;
    posix_spawnattr_t attr;
    posix_spawn_file_actions_t actions;
    int e = posix_spawnattr_init(&attr);
    if (e) return e;
    e = posix_spawn_file_actions_init(&actions);
    if (e) { posix_spawnattr_destroy(&attr); return e; }
    /* Unchanged stdio uses Darwin's explicit inherit action, including for SETEXEC. Only a child
       remap needs copies: its actions must not clobber a later source if 0, 1 or 2 was initially closed. */
    int source[3] = {-1, -1, -1};
    for (int i = 0; !e && i < 3; i++) {
        const int fd = standard ? standard[i] : i;
        if (fd < 0) continue;
        if (!standard) {
            if (fcntl(fd, F_GETFD) >= 0) e = posix_spawn_file_actions_addinherit_np(&actions, fd);
            else if (errno != EBADF) e = errno;
            continue;
        }
        source[i] = fcntl(fd, F_DUPFD_CLOEXEC, 3);
        if (source[i] < 0) {
            /* Each explicit source belongs to the caller and must stay open until this call returns. */
            e = errno;
        } else e = posix_spawn_file_actions_adddup2(&actions, source[i], i);
    }
    sigset_t mask, defaults;
    sigemptyset(&mask);
    sigemptyset(&defaults);
    sigaddset(&defaults, SIGPIPE);
    if (!e) e = posix_spawnattr_setsigmask(&attr, &mask);
    if (!e) e = posix_spawnattr_setsigdefault(&attr, &defaults);
    const short flags = POSIX_SPAWN_CLOEXEC_DEFAULT | POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF |
                        (replace ? POSIX_SPAWN_SETEXEC : 0);
    if (!e) e = posix_spawnattr_setflags(&attr, flags);
    if (!e) e = posix_spawnp(pid, path, &actions, &attr, argv, envp);
    if (e && pid) *pid = 0;
    for (int i = 0; i < 3; i++) if (source[i] >= 0) close(source[i]);
    posix_spawn_file_actions_destroy(&actions);
    posix_spawnattr_destroy(&attr);
    return e;                       /* a successful SETEXEC never returns */
}
int darwin_pipe_cloexec(int fd[2]) {
    if (pipe(fd)) return errno;
    int e = 0;
    for (int i = 0; i < 2 && !e; i++) {
        const int flags = fcntl(fd[i], F_GETFD);
        if (flags < 0 || fcntl(fd[i], F_SETFD, flags | FD_CLOEXEC)) e = errno;
    }
    /* Darwin's per-descriptor suppression works for pipes too, without changing another thread's
       signal disposition or swallowing a SIGPIPE that was already pending in the caller. */
    if (!e && fcntl(fd[1], F_SETNOSIGPIPE, 1)) e = errno;
    if (e) { close(fd[0]); close(fd[1]); fd[0] = fd[1] = -1; }
    return e;
}
int darwin_wait_child(pid_t pid, int *status) {
    if (pid <= 0 || !status) return EINVAL;
    pid_t got;
    do { got = waitpid(pid, status, 0); } while (got < 0 && errno == EINTR);
    return got == pid ? 0 : errno;
}
static void *reap(void *arg) {
    const pid_t pid = *(pid_t *)arg;
    free(arg);
    int status;
    (void)darwin_wait_child(pid, &status);
    return NULL;
}
int darwin_reap_child(pid_t pid) {
    if (pid <= 0) return EINVAL;
    pid_t *arg = malloc(sizeof *arg);
    if (arg) {
        *arg = pid;
        pthread_attr_t attr;
        int e = pthread_attr_init(&attr);
        if (!e) {
            e = pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_DETACHED);
            pthread_t thread;
            if (!e) e = pthread_create(&thread, &attr, reap, arg);
            pthread_attr_destroy(&attr);
        }
        if (!e) return 0;
        free(arg);
    }
    int status;
    return darwin_wait_child(pid, &status);
}
#endif
