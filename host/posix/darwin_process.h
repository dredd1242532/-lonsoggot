/* SPDX-License-Identifier: GPL-3.0-or-later */
#pragma once
#if defined(__APPLE__)
#include <stddef.h>
#include <sys/types.h>
#ifdef __cplusplus
extern "C" {
#endif
/* Error-number results (zero is success); no truncated paths. */
int darwin_executable_path(char *out, size_t cap);
/* Only the listed standard descriptors survive. -1 leaves that descriptor closed; NULL preserves the
   currently open standard descriptors. The kernel closes all others, including descriptors created
   concurrently. replace requires standard == NULL and keeps the PID with unchanged stdio (SETEXEC). */
int darwin_spawn(pid_t *pid, const char *path, char *const argv[], char *const envp[], const int standard[3], int replace);
int darwin_pipe_cloexec(int fd[2]);
int darwin_wait_child(pid_t pid, int *status);
/* Starts a detached waiter; if resources prevent it, waits here instead. Never abandons a spawned child. */
int darwin_reap_child(pid_t pid);
#ifdef __cplusplus
}
#endif
#endif
