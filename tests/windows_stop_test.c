/* Win32 API stand-ins only: no real window, console, process exit or game. */
#define _CRT_SECURE_NO_WARNINGS
#include <assert.h>
#include <setjmp.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

typedef int BOOL;
typedef int32_t LONG;
typedef uint32_t DWORD, UINT;
typedef uint64_t ULONGLONG;
typedef uintptr_t WPARAM;
typedef intptr_t LPARAM, LRESULT;
typedef void *HANDLE, *HWND, *HINSTANCE;
typedef struct { LRESULT (*lpfnWndProc)(HWND, UINT, WPARAM, LPARAM); HINSTANCE hInstance; const wchar_t *lpszClassName; } WNDCLASSW;
typedef struct { UINT message; } MSG;
#define WINAPI
#define CALLBACK
#define TRUE 1
#define FALSE 0
#define INFINITE UINT32_MAX
#define WAIT_OBJECT_0 0
#define WAIT_TIMEOUT 258
#define CTRL_C_EVENT 0
#define CTRL_BREAK_EVENT 1
#define CTRL_CLOSE_EVENT 2
#define CTRL_LOGOFF_EVENT 5
#define CTRL_SHUTDOWN_EVENT 6
#define WM_CLOSE 16
#define WM_QUERYENDSESSION 17
#define WM_ENDSESSION 22
#define WS_EX_TOOLWINDOW 0x80
#define WS_EX_NOACTIVATE 0x08000000
#define WS_OVERLAPPED 0
#define QS_ALLINPUT 0x4ff
#define PM_REMOVE 1

static ULONGLONG now;
static int requested, signals, sleeps, work, flushes, closes, default_calls, windows, registrations, event_calls, thread_calls;
static int fail_event, fail_thread, force_code, in_handler, inject_request, flood, messages_seen;
static DWORD last_sleep;
static jmp_buf escaped;
static BOOL (*handler)(DWORD);
static DWORD (*threads[2])(void *);
static char logs[2048];
static LONG InterlockedCompareExchange(volatile LONG *value, LONG set, LONG expected) {
    LONG old = *value;
    if (old == expected) *value = set;
    return old;
}
static ULONGLONG GetTickCount64(void) { return now; }
static BOOL SetEvent(HANDLE event) {
    if (event == (HANDLE)(uintptr_t)1) { requested = 1; signals++; }
    return TRUE;
}
static DWORD WaitForSingleObject(HANDLE event, DWORD ms) {
    (void)ms;
    if (event == (HANDLE)(uintptr_t)1) return requested ? WAIT_OBJECT_0 : WAIT_TIMEOUT;
    return WAIT_OBJECT_0;
}
static void Sleep(DWORD ms) { last_sleep = ms; sleeps++; now += ms; }
static LRESULT DefWindowProcW(HWND w, UINT message, WPARAM wp, LPARAM lp) {
    (void)w; (void)message; (void)wp; (void)lp; default_calls++; return 99;
}
void hos_log(const char *format, ...) {
    assert(!in_handler);
    va_list ap;
    va_start(ap, format);
    size_t n = strlen(logs);
    vsnprintf(logs + n, sizeof logs - n, format, ap);
    va_end(ap);
}
void hos_log_flush(void) { assert(!in_handler); flushes++; }
void rx_close_asked(void) { assert(!in_handler); work++; }
void hos_window_closed_exit(void) { assert(!in_handler && work == 1); closes++; longjmp(escaped, 1); }
static HINSTANCE GetModuleHandleW(const wchar_t *name) { assert(!name); return (HINSTANCE)(uintptr_t)3; }
static int RegisterClassW(const WNDCLASSW *wc) { assert(wc->lpfnWndProc); return 1; }
static HWND CreateWindowExW(DWORD ex, const wchar_t *cls, const wchar_t *title, DWORD style,
                            int x, int y, int w, int h, HWND parent, HANDLE menu, HINSTANCE instance, void *arg) {
    (void)cls; (void)title; (void)x; (void)y; (void)w; (void)h; (void)menu; (void)instance; (void)arg;
    assert(ex == (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE));
    assert(style == WS_OVERLAPPED && parent == NULL); /* hidden, top-level, not HWND_MESSAGE */
    windows++;
    return (HWND)(uintptr_t)4;
}
static DWORD GetLastError(void) { return 8; }
static BOOL SetConsoleCtrlHandler(BOOL (*fn)(DWORD), BOOL add) {
    if (!fn) { assert(!add); return TRUE; }
    assert(add); handler = fn; registrations++; return TRUE;
}
static HANDLE GetCurrentProcess(void) { return (HANDLE)(uintptr_t)5; }
static BOOL TerminateProcess(HANDLE process, UINT code) {
    assert(!in_handler && process == GetCurrentProcess());
    force_code = (int)code;
    longjmp(escaped, 2);
}
static DWORD MsgWaitForMultipleObjects(DWORD n, const HANDLE *events, BOOL all, DWORD ms, DWORD mask) {
    (void)events;
    assert(!all && mask == QS_ALLINPUT);
    if (inject_request) { assert(n == 1 && ms == INFINITE); inject_request = 0; handler(CTRL_C_EVENT); }
    else { assert(n == 0 && ms <= 4000); now += flood ? 250 : ms; }
    return 0;
}
static BOOL PeekMessageW(MSG *msg, HWND window, UINT low, UINT high, UINT mode) {
    (void)window; (void)low; (void)high; assert(mode == PM_REMOVE);
    msg->message = 0;
    if (flood) { messages_seen++; return TRUE; }
    return FALSE;
}
static BOOL TranslateMessage(const MSG *msg) { (void)msg; return TRUE; }
static LRESULT DispatchMessageW(const MSG *msg) { (void)msg; return 0; }
static HANDLE CreateEventW(void *security, BOOL manual, BOOL initial, const wchar_t *name) {
    assert(!security && manual && !initial && !name);
    event_calls++;
    return event_calls == fail_event ? NULL : (HANDLE)(uintptr_t)event_calls;
}
static HANDLE CreateThread(void *security, size_t stack, DWORD (*fn)(void *), void *arg, DWORD flags, DWORD *id) {
    assert(!security && !stack && !arg && !flags && !id);
    assert(thread_calls < 2);
    threads[thread_calls++] = fn;
    return thread_calls == fail_thread ? NULL : (HANDLE)(uintptr_t)(10 + thread_calls);
}
static BOOL CloseHandle(HANDLE handle) { assert(handle); return TRUE; }

/* ACTUAL_WINDOWS_STOP */

static void reset(void) {
    now = 100;
    stop_requested = (HANDLE)(uintptr_t)1;
    stop_ready = (HANDLE)(uintptr_t)2;
    stop_reason = 0; stop_at = 0;
    requested = signals = sleeps = work = flushes = closes = default_calls = windows = registrations = 0;
    event_calls = thread_calls = fail_event = fail_thread = force_code = in_handler = inject_request = flood = messages_seen = 0;
    last_sleep = 0; logs[0] = 0;
}

int main(void) {
    const DWORD events[] = {CTRL_C_EVENT, CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT};
    for (size_t i = 0; i < sizeof events / sizeof events[0]; i++) {
        reset();
        in_handler = 1;
        assert(!stop_console(99) && !requested);
        assert(stop_console(events[i]));
        assert(signals == 1 && stop_reason == (LONG)i + 1 && stop_at == 100);
        assert(work == 0 && closes == 0 && flushes == 0 && logs[0] == 0);
        assert(sleeps == (i >= 2) && (i < 2 || last_sleep == STOP_WAIT_MS));
        assert(stop_console(CTRL_BREAK_EVENT));
        assert(signals == 1 && stop_reason == (LONG)i + 1 && stop_at == 100);
        in_handler = 0;
        if (!setjmp(escaped)) stop_worker(NULL);
        assert(work == 1 && closes == 1 && flushes == 1);
        assert(strstr(logs, "incomplete until normal-close marker") && strstr(logs, "timeout exit 124"));
    }
    puts("PASS: five console events request once; handlers do no close/log work; repeats preserve deadline");

    reset();
    assert(stop_window(NULL, WM_QUERYENDSESSION, 0, 0) == TRUE);
    assert(stop_window(NULL, WM_ENDSESSION, 0, 0) == 0 && signals == 0);
    assert(stop_window(NULL, 99, 0, 0) == 99 && default_calls == 1);
    in_handler = 1;
    assert(stop_window(NULL, WM_CLOSE, 0, 0) == 0 && signals == 1 && work == 0);
    assert(stop_window(NULL, WM_CLOSE, 0, 0) == 0 && signals == 1);
    now += 1500;
    assert(stop_window(NULL, WM_ENDSESSION, 1, 0) == 0 && last_sleep == 2500);
    in_handler = 0;
    assert(stop_remaining() == 0 && stop_at == 100);
    reset();
    assert(stop_window(NULL, WM_ENDSESSION, 1, 0) == 0);
    assert(stop_reason == STOP_SESSION && signals == 1 && last_sleep == STOP_WAIT_MS);
    puts("PASS: hidden-window close, cancelled/confirmed end-session and original deadline");

    for (int mode = 0; mode < 2; mode++) {
        reset();
        inject_request = 1; flood = mode;
        if (!setjmp(escaped)) stop_messages(NULL);
        assert(force_code == STOP_TIMEOUT_EXIT && now == 4100);
        assert(windows == 1 && registrations == 1 && signals == 1 && work == 0 && logs[0] == 0);
        assert(!mode || messages_seen > 32);
    }
    puts("PASS: hung close bounded without log locks, including a flooded message queue (exit 124)");

    reset();
    assert(hos_windows_stop_start() && event_calls == 2 && thread_calls == 2);
    assert(threads[0] == stop_worker && threads[1] == stop_messages);
    for (int i = 1; i <= 2; i++) {
        reset(); fail_event = i;
        assert(!hos_windows_stop_start() && thread_calls == 0);
        reset(); fail_thread = i;
        assert(!hos_windows_stop_start() && thread_calls == i);
    }
    puts("PASS: startup owns both workers; event/thread failures reported");
    return 0;
}
