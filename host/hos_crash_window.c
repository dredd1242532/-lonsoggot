/* (longshot) The crash window (docs/crash-report-window.md). At a crash nothing handles, report_crash (host/mkw_glue.c)
   writes the CRASH: lines and flushes the log, then calls hos_crash_window_open: it leaves crash_pending.txt in the game
   folder and starts the Longshot Launcher's crash window, <launcher> --crash <game folder>, which shows the player the
   report and sends it only when they press Send Help (LongshotLauncher's crash window, which runs nx2/crash_report.py).

   The window is another process: a broken heap, a lost GPU or a stuck thread in this one cannot take it down. And this
   one does as little as it can: the command is made at startup (hos_crash_window_init), so the handler allocates nothing
   and reads no file. It writes the marker and starts the process:
     Windows: CreateProcessW, detached, as the unhandled exception filter may;
     Linux: vfork and execve (report_crash runs in the signal handler there: fork would run glibc's fork handlers, which
     take malloc's lock, and a crash inside malloc would hang on it). The window's output goes to /dev/null, not into the
     log the report reads. The child also clears its signal mask (the crash's signal is blocked in the handler, and
     execve keeps the mask: the window, and a game it starts with Play, would die on a fault with no handler run) and
     closes the game's other files (the log, sockets, the GPU device: the window outlives the game).
   If the window doesn't start (the launcher moved, the process was killed before), the marker stays, and the launcher
   offers the report at its next start.

   The launcher is longshot_version.txt's builder= (and builder_args=, when a builder needs arguments before --crash), as
   nx2/make_game.py writes it from LONGSHOT_BUILDER_EXE: the launcher that built the folder. Nothing happens with
   crash_reports=0 in longshot_options.ini, LONGSHOT_NO_CRASH_WINDOW=1 (test runs, benchmarks), or no builder= line (a
   folder built from a checkout without LONGSHOT_BUILDER_EXE). */
#include "hos_paths.h"   /* (first: hos_game_path_a, and windows.h on Windows) */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "hos_crash_window.h"
#include "../gpu/vk/compat/longshot_env.h"

void hos_log(const char *fmt, ...);

#define CW_PATH 4096
#ifdef _WIN32
#define CW_SEP '\\'
static wchar_t marker_w[CW_PATH];
static wchar_t command_w[CW_PATH * 3];   /* (CreateProcessW may write into it) */
#else
#include <fcntl.h>
#include <signal.h>
#include <sys/syscall.h>
#include <unistd.h>
#define CW_SEP '/'
extern char **environ;
static char marker_path[CW_PATH];
static char builder[CW_PATH], folder_arg[CW_PATH], crash_flag[] = "--crash";
static char *window_argv[4];
#endif
static volatile LONG ready;

/* longshot_version.txt's value for key ("builder="), "" when there is none */
static void version_value(const char *folder, const char *key, char *out, size_t cap) {
    char path[CW_PATH + 32];
    out[0] = 0;
    snprintf(path, sizeof path, "%s%clongshot_version.txt", folder, CW_SEP);
    FILE *f = fopen(path, "r");
    if (!f) return;
    char line[CW_PATH];
    const size_t k = strlen(key);
    while (fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = 0;
        if (!strncmp(line, key, k) && strlen(line + k) < cap) {
            strcpy(out, line + k);
            break;
        }
    }
    fclose(f);
}

/* crash_reports=0 in longshot_options.ini: the player turned the crash window off. Not hos_ini_option (host/nn_applet.c),
   which reads the options beside the game's executable: init_in reads the folder it is given (the tests' own) */
static int turned_off(const char *folder) {
    char path[CW_PATH + 32];
    snprintf(path, sizeof path, "%s%clongshot_options.ini", folder, CW_SEP);
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    char line[256];
    int off = 0;
    while (fgets(line, sizeof line, f))
        if (!strncmp(line, "crash_reports=", 14)) off = atoi(line + 14) == 0;
    fclose(f);
    return off;
}

void hos_crash_window_init_in(const char *folder) {
    InterlockedExchange(&ready, 0);
    if (env_on("LONGSHOT_NO_CRASH_WINDOW")) {   /* (the switches' one rule: 0, false, off or no is off, longshot_env.h) */
        hos_log("crash window: off (LONGSHOT_NO_CRASH_WINDOW)");
        return;
    }
    if (turned_off(folder)) {
        hos_log("crash window: off (crash_reports=0)");
        return;
    }
    char exe[CW_PATH], args[CW_PATH];
    version_value(folder, "builder=", exe, sizeof exe);
    if (!exe[0]) {
        hos_log("crash window: no builder= in longshot_version.txt: a crash is offered at the launcher's next start instead");
        return;
    }
    version_value(folder, "builder_args=", args, sizeof args);
    char marker[CW_PATH];
    if ((size_t)snprintf(marker, sizeof marker, "%s%ccrash_pending.txt", folder, CW_SEP) >= sizeof marker) return;
#ifdef _WIN32
    /* (one command line: the builder quoted, its own arguments as builder_args= has them, then --crash and the folder) */
    char line[CW_PATH * 3];
    if ((size_t)snprintf(line, sizeof line, "\"%s\" %s%s--crash \"%s\"", exe, args, args[0] ? " " : "", folder) >= sizeof line)
        return;
    if (!MultiByteToWideChar(CP_UTF8, 0, line, -1, command_w, (int)(sizeof command_w / sizeof *command_w)) ||
        !MultiByteToWideChar(CP_UTF8, 0, marker, -1, marker_w, (int)(sizeof marker_w / sizeof *marker_w)))
        return;
#else
    (void)args;   /* (the launcher's AppImage takes --crash itself) */
    if (strlen(exe) >= sizeof builder || strlen(folder) >= sizeof folder_arg) return;
    strcpy(builder, exe);
    strcpy(folder_arg, folder);
    strcpy(marker_path, marker);
    window_argv[0] = builder, window_argv[1] = crash_flag, window_argv[2] = folder_arg, window_argv[3] = NULL;
#endif
    InterlockedExchange(&ready, 1);
    hos_log("crash window: on a crash, %s --crash <this folder>", exe);
}

void hos_crash_window_init(void) {
    char path[CW_PATH];
    const DWORD n = hos_game_path_a(path, sizeof path);
    if (!n || n >= sizeof path) return;
    char *end = path + n;
    while (end > path && end[-1] != '\\' && end[-1] != '/') end--;   /* (the folder: hos_game_path_a's, above bin/) */
    while (end > path + 1 && (end[-1] == '\\' || end[-1] == '/')) end--;
    *end = 0;
    hos_crash_window_init_in(path);
}

/* "log=longshot_log.txt\ntime=<seconds>\n", without printf (Linux: a signal handler) */
static int marker_text(char *out, size_t cap) {
    static const char head[] = "log=longshot_log.txt\ntime=";
    char digits[24];
    int n = 0;
    unsigned long long t = (unsigned long long)time(NULL);
    do digits[n++] = (char)('0' + t % 10), t /= 10;
    while (t && n < (int)sizeof digits);
    const size_t len = sizeof head - 1 + (size_t)n + 1;
    if (len >= cap) return 0;
    memcpy(out, head, sizeof head - 1);
    for (int i = 0; i < n; i++) out[sizeof head - 1 + i] = digits[n - 1 - i];
    out[len - 1] = '\n';
    return (int)len;
}

void hos_crash_window_open(void) {
    if (!InterlockedExchange(&ready, 0)) return;   /* (once: a second thread's crash starts no second window) */
    char text[96];
    const int len = marker_text(text, sizeof text);
#ifdef _WIN32
    HANDLE h = CreateFileW(marker_w, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h != INVALID_HANDLE_VALUE) {
        DWORD wrote;
        WriteFile(h, text, (DWORD)len, &wrote, NULL);
        CloseHandle(h);
    }
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    memset(&si, 0, sizeof si);
    si.cb = sizeof si;
    if (CreateProcessW(NULL, command_w, NULL, NULL, FALSE, DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, NULL, NULL, &si, &pi)) {
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
    }
#else
    const int fd = open(marker_path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd >= 0) {
        if (write(fd, text, (size_t)len) < 0) { /* (the next launch finds no marker: nothing worse) */ }
        close(fd);
    }
    const pid_t child = vfork();
    if (child == 0) {
        setsid();   /* (its own session: it outlives this process, and a terminal's Ctrl+C doesn't reach it) */
        const int null = open("/dev/null", O_RDWR);
        if (null >= 0) dup2(null, 0), dup2(null, 1), dup2(null, 2);
        sigset_t none;   /* (sigprocmask and close are async-signal-safe; close_range is one system call, Linux 5.9) */
        sigemptyset(&none);
        sigprocmask(SIG_SETMASK, &none, NULL);
#ifdef SYS_close_range
        if (syscall(SYS_close_range, 3u, ~0u, 0u) != 0)
#endif
            for (int fd = 3; fd < 4096; fd++) close(fd);
        execve(builder, window_argv, environ);
        _exit(127);
    }
#endif
}
