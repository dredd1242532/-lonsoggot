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
#include "coop_profile.h"

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

static int relaunch(void) {
    wchar_t exe[MAX_PATH * 4];
    const DWORD n = GetModuleFileNameW(NULL, exe, MAX_PATH * 4);
    if (!n || n >= MAX_PATH * 4) {
        hos_log("restart: this executable's path was not found: the game only exits");
        return 0;
    }
    const wchar_t *line = GetCommandLineW();
    const size_t len = wcslen(line);
    wchar_t *cmd = (wchar_t *)malloc((len + 1) * sizeof *cmd);   /* (CreateProcessW may write into it) */
    if (!cmd) return 0;
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
    const char *ticket = coop_profile_restart_token();
    wchar_t value[33];
    for (unsigned i = 0; i <= strlen(ticket); i++) value[i] = (unsigned char)ticket[i];
    wchar_t *profile_env = env ? hos_relaunch_env_block(env, L"HOS_COOP_PROFILE_TICKET", value) : NULL;
    if (!profile_env) { free(cmd); free(env); return 0; }
    free(env);
    env = profile_env;
    const int started = CreateProcessW(exe, cmd, NULL, NULL, FALSE, CREATE_UNICODE_ENVIRONMENT, env, NULL, &si, &pi) != 0;
    if (started) {
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
    } else {
        hos_log("restart: the game could not be started again (error %lu): it only exits", (unsigned long)GetLastError());
        hos_log_flush();
    }
    free(cmd);
    free(env);
    return started;
}
#else
#include <dirent.h>
#include <fcntl.h>
#include <unistd.h>

extern char **environ;

static int relaunch(void) {
    HosRelaunch r;
    if (!hos_relaunch_command("/proc/self/cmdline", "/proc/self/environ", &r)) {
        hos_log("restart: this process's command was not found: the game only exits");
        hos_log_flush();
        return 0;
    }
    /* Replace rather than inherit an earlier one-use ticket from /proc/environ. */
    const char *ticket = coop_profile_restart_token();
    char ticket_env[64];
    snprintf(ticket_env, sizeof ticket_env, "HOS_COOP_PROFILE_TICKET=%s", ticket);
    char **env = calloc((size_t)r.envc + 2, sizeof *env);
    if (!env) return 0;
    int count = 0;
    for (int i = 0; i < r.envc; i++)
        if (strncmp(r.envp[i], "HOS_COOP_PROFILE_TICKET=", 23)) env[count++] = r.envp[i];
    if (*ticket) env[count++] = ticket_env;
    r.envp = env;
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
    return 0;
}
#endif

/* The window was closed (host/display.c's WM_CLOSE, host/posix/display_sdl.c's quit): the game exits, and starts again
   first when the menu asked for it */
void hos_window_closed_exit(void) {
    const int again = relaunch_asked != 0;
    hos_log(again ? "display: window closed for a restart (the Insert menu's Restart now)" : "display: window closed, exiting");
    void coop_shutdown(void);
    coop_shutdown();   /* (a co-op room's BYE and LEAVE, at most 0.4 s: host/coop.c) */
    hos_log_flush();
    void hos_crash_clean_end(void);
    hos_crash_clean_end();
    if (!again || !relaunch()) coop_profile_restart_failed();
    ExitProcess(0);
}
