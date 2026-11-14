/* (longshot) The Insert menu's Restart now (gpu/vk/vk_menu.cpp): it saves its options and asks the window to close for a
   restart (WM_APP + 9: host/display.c, host/posix/display_sdl.c). The window closes as when the player closes it, and at
   the very end of that, instead of only exiting, the game is started again:
     Linux: the process becomes the game again (execve) with the arguments and the environment it started with
     (/proc/self/cmdline and /proc/self/environ: longshot_env.txt's lines are read again, not kept), so whatever waits
     for it (the launcher, a stream, a test) still has it. The game is bin/longshot on disk, never run from inside the
     AppImage (longshot-launch starts it with the AppImage's variables taken away), so /proc/self/exe is the game;
     argv[0] on the PATH if that is gone (the folder built again meanwhile).
     Windows: a new process of this executable (GetModuleFileNameW) with this one's command line (GetCommandLineW), in
     the same folder, with the environment this one started with (kept by hos_relaunch_keep_env before longshot_env.txt
     was read: the file's lines are read again, and its edits since are taken, not left out as the environment's own);
     then this one exits. */
#include "hos.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>
#include "hos_relaunch.h"

static volatile LONG relaunch_asked;

void hos_relaunch_ask(void) { InterlockedExchange(&relaunch_asked, 1); }

#ifdef _WIN32
static wchar_t *start_env;   /* (the environment block at the start: hos_relaunch_keep_env) */

void hos_relaunch_keep_env(void) {
    wchar_t *b = GetEnvironmentStringsW();
    if (!b) return;
    size_t n = 0;
    while (b[n]) n += wcslen(b + n) + 1;
    start_env = (wchar_t *)malloc((n + 1) * sizeof *start_env);
    if (start_env) memcpy(start_env, b, (n + 1) * sizeof *start_env);
    FreeEnvironmentStringsW(b);
}

static void relaunch(void) {
    wchar_t exe[MAX_PATH * 4];
    const DWORD n = GetModuleFileNameW(NULL, exe, MAX_PATH * 4);
    if (!n || n >= MAX_PATH * 4) {
        hos_log("restart: this executable's path was not found: the game only exits");
        return;
    }
    const wchar_t *line = GetCommandLineW();
    const size_t len = wcslen(line);
    wchar_t *cmd = (wchar_t *)malloc((len + 1) * sizeof *cmd);   /* (CreateProcessW may write into it) */
    if (!cmd) return;
    memcpy(cmd, line, (len + 1) * sizeof *cmd);
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    memset(&si, 0, sizeof si);
    si.cb = sizeof si;
    /* (the new process waits for this one to end before it moves longshot_log.txt to .1 and opens its own: this one still
       has it open until it exits, and a rename of an open file fails on Windows, so the new run overwrote the old run's
       log. hos_main.c reads HOS_RESTART_OF) */
    wchar_t pid[16];
    swprintf(pid, 16, L"%lu", (unsigned long)GetCurrentProcessId());
    SetEnvironmentVariableW(L"HOS_RESTART_OF", pid);
    wchar_t *env = start_env ? hos_relaunch_env_block(start_env, L"HOS_RESTART_OF", pid) : NULL;   /* (NULL: this one's) */
    if (CreateProcessW(exe, cmd, NULL, NULL, FALSE, env ? CREATE_UNICODE_ENVIRONMENT : 0, env, NULL, &si, &pi)) {
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
    } else {
        hos_log("restart: the game could not be started again (error %lu): it only exits", (unsigned long)GetLastError());
        hos_log_flush();
    }
    free(cmd);
    free(env);
}
#elif defined(__APPLE__)
#include "posix/darwin_process.h"
#include <errno.h>
#include <limits.h>
#include <stdint.h>

static HosRelaunch start;
static char start_exe[PATH_MAX];
static int start_error = EINVAL;

static int copy_strings(char *const from[], size_t n, char **blob, char ***vector) {
    size_t bytes = 0;
    if (n > SIZE_MAX / sizeof(char *) - 1) return EOVERFLOW;
    for (size_t i = 0; i < n; i++) {
        if (!from[i]) return EINVAL;
        const size_t len = strlen(from[i]) + 1;
        if (bytes > SIZE_MAX - len) return EOVERFLOW;
        bytes += len;
    }
    char *b = malloc(bytes ? bytes : 1);
    char **v = malloc((n + 1) * sizeof *v);
    if (!b || !v) { free(b); free(v); return ENOMEM; }
    char *at = b;
    for (size_t i = 0; i < n; i++) {
        const size_t len = strlen(from[i]) + 1;
        v[i] = at;
        memcpy(at, from[i], len);
        at += len;
    }
    v[n] = NULL;
    *blob = b;
    *vector = v;
    return 0;
}
int hos_relaunch_keep_startup(int argc, char *const argv[], char *const envp[]) {
    if (start.argv) return 0;        /* never replace the initial snapshot with a later, mutated one */
    if (argc < 1 || !argv || !envp) return start_error = EINVAL;
    size_t envc = 0;
    while (envp[envc]) {
        if (envc == INT_MAX) return start_error = EOVERFLOW;
        envc++;
    }
    HosRelaunch saved = {0};
    int e = darwin_executable_path(start_exe, sizeof start_exe);
    if (!e) e = copy_strings(argv, (size_t)argc, &saved.args, &saved.argv);
    if (!e) e = copy_strings(envp, envc, &saved.env, &saved.envp);
    if (e) {
        free(saved.args); free(saved.argv); free(saved.env); free(saved.envp);
        return start_error = e;
    }
    saved.argc = argc;
    saved.envc = (int)envc;
    start = saved;
    return start_error = 0;
}
int hos_relaunch_exec_startup(void) {
    if (!start.argv) return start_error;
    fflush(NULL);
    pid_t pid;
    return darwin_spawn(&pid, start_exe, start.argv, start.envp, NULL, 1);
}
static void relaunch(void) {
    const int e = hos_relaunch_exec_startup();
    hos_log("restart: the game could not be started again (%s): it only exits", strerror(e));
    hos_log_flush();
}
#else
#include <dirent.h>
#include <fcntl.h>
#include <unistd.h>

extern char **environ;

static void relaunch(void) {
    HosRelaunch r;
    if (!hos_relaunch_command("/proc/self/cmdline", "/proc/self/environ", &r)) {
        hos_log("restart: this process's command was not found: the game only exits");
        hos_log_flush();
        return;
    }
    /* (every file this process opened closes with it, as at an exit: the new one opens its own) */
    DIR *d = opendir("/proc/self/fd");
    if (d) {
        const int skip = dirfd(d);
        for (struct dirent *e; (e = readdir(d));) {
            const int fd = atoi(e->d_name);
            if (fd > 2 && fd != skip) fcntl(fd, F_SETFD, fcntl(fd, F_GETFD) | FD_CLOEXEC);
        }
        closedir(d);
    }
    fflush(NULL);
    execve("/proc/self/exe", r.argv, r.envp);
    environ = r.envp;   /* (execvp searches the PATH of the environment it is given: the one the game started with) */
    execvp(r.argv[0], r.argv);
    hos_log("restart: the game could not be started again: it only exits");
    hos_log_flush();
}
#endif

/* The window was closed (host/display.c's WM_CLOSE, host/posix/display_sdl.c's quit): the game exits, and starts again
   first when the menu asked for it */
void hos_window_closed_exit(void) {
    const int again = relaunch_asked != 0;
    hos_log(again ? "display: window closed for a restart (the Insert menu's Restart now)" : "display: window closed, exiting");
    hos_log_flush();
    if (again) relaunch();
    ExitProcess(0);
}
