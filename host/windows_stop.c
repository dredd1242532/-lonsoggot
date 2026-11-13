/* Windowless Windows stop requests. No renderer, save or log work runs on a console handler thread.
   The hidden top-level window is never shown: unlike HWND_MESSAGE, it receives enumerated/broadcast messages.
   The worker uses the normal close path. The other thread bounds it, including a stuck renderer or ExitProcess.
   This is NOT a save barrier; see docs/development.md, Stopping a windowless game. */
#ifdef _WIN32
#include <windows.h>

void hos_log(const char *fmt, ...);
void hos_log_flush(void);
void rx_close_asked(void);
void hos_window_closed_exit(void);

enum { STOP_WAIT_MS = 4000, STOP_TIMEOUT_EXIT = 124 };
enum { STOP_CTRL_C = 1, STOP_CTRL_BREAK, STOP_CONSOLE_CLOSE, STOP_LOGOFF, STOP_SHUTDOWN, STOP_WINDOW, STOP_SESSION };
static HANDLE stop_requested, stop_ready;
static volatile LONG stop_reason;
static ULONGLONG stop_at;   /* published by the winning requester before it signals stop_requested */

static void request_stop(LONG reason) {
    if (InterlockedCompareExchange(&stop_reason, reason, 0) != 0) return;
    stop_at = GetTickCount64();
    SetEvent(stop_requested);
}

static DWORD stop_remaining(void) {
    const ULONGLONG elapsed = GetTickCount64() - stop_at;
    return elapsed < STOP_WAIT_MS ? STOP_WAIT_MS - (DWORD)elapsed : 0;
}

static BOOL WINAPI stop_console(DWORD event) {
    LONG reason;
    switch (event) {
    case CTRL_C_EVENT: reason = STOP_CTRL_C; break;
    case CTRL_BREAK_EVENT: reason = STOP_CTRL_BREAK; break;
    case CTRL_CLOSE_EVENT: reason = STOP_CONSOLE_CLOSE; break;
    case CTRL_LOGOFF_EVENT: reason = STOP_LOGOFF; break;
    case CTRL_SHUTDOWN_EVENT: reason = STOP_SHUTDOWN; break;
    default: return FALSE;
    }
    request_stop(reason);
    /* Returning from CLOSE/LOGOFF/SHUTDOWN ends the process even with TRUE. Leave the normal worker time to
       finish instead. Windows may impose a shorter deadline; never promise that its shutdown can be postponed. */
    if (event != CTRL_C_EVENT && event != CTRL_BREAK_EVENT) Sleep(STOP_WAIT_MS);
    return TRUE;   /* repeats do not start another close or extend its deadline */
}

static LRESULT CALLBACK stop_window(HWND window, UINT message, WPARAM wp, LPARAM lp) {
    switch (message) {
    case WM_CLOSE:
        request_stop(STOP_WINDOW);
        return 0;
    case WM_QUERYENDSESSION:
        return TRUE;   /* a query can be cancelled: don't quit until WM_ENDSESSION(TRUE) */
    case WM_ENDSESSION:
        if (wp) {
            request_stop(STOP_SESSION);
            /* Returning allows Windows to end the process. The worker is independent of this message pump. */
            WaitForSingleObject(stop_requested, INFINITE);
            Sleep(stop_remaining());
        }
        return 0;
    }
    return DefWindowProcW(window, message, wp, lp);
}

static DWORD WINAPI stop_worker(void *unused) {
    (void)unused;
    WaitForSingleObject(stop_requested, INFINITE);
    static const char *const reasons[] = {"", "Ctrl+C", "Ctrl+Break", "console close", "console logoff",
                                         "console shutdown", "WM_CLOSE", "WM_ENDSESSION"};
    hos_log("stop: requested by %s; incomplete until normal-close marker and process exit 0; deadline 4000 ms (timeout exit 124)",
            reasons[InterlockedCompareExchange(&stop_reason, 0, 0)]);
    hos_log_flush();
    rx_close_asked();
    hos_window_closed_exit();
    return 0;
}

static DWORD WINAPI stop_messages(void *unused) {
    (void)unused;
    WNDCLASSW wc = {0};
    wc.lpfnWndProc = stop_window;
    wc.hInstance = GetModuleHandleW(NULL);
    wc.lpszClassName = L"LongshotHeadlessStop";
    if (!RegisterClassW(&wc) || !CreateWindowExW(WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE, wc.lpszClassName,
            L"Longshot windowless stop", WS_OVERLAPPED, 0, 0, 0, 0, NULL, NULL, wc.hInstance, NULL)) {
        hos_log("stop: hidden window unavailable (error %lu); use the control bridge", (unsigned long)GetLastError());
    }
    /* CREATE_NEW_PROCESS_GROUP can leave Ctrl+C ignored by inheritance. Opt this process back in. */
    if (!SetConsoleCtrlHandler(NULL, FALSE) || !SetConsoleCtrlHandler(stop_console, TRUE))
        hos_log("stop: console handler unavailable (error %lu); use the control bridge", (unsigned long)GetLastError());
    SetEvent(stop_ready);
    int requested = 0;
    for (;;) {
        if (!requested && WaitForSingleObject(stop_requested, 0) == WAIT_OBJECT_0) requested = 1;
        /* No logging here: a blocked log writer must not defeat the deadline. The request line and absence of
           the normal-close marker distinguish incomplete work; the owner's exit receipt records timeout 124. */
        DWORD remaining = requested ? stop_remaining() : INFINITE;
        if (!remaining) {
            TerminateProcess(GetCurrentProcess(), STOP_TIMEOUT_EXIT);
            return 0;
        }
        MsgWaitForMultipleObjects(requested ? 0 : 1, &stop_requested, FALSE, remaining, QS_ALLINPUT);
        MSG msg;
        /* Bound each batch: a stream of messages cannot starve the deadline. */
        for (int n = 0; n < 32 && PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE); n++) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
}

int hos_windows_stop_start(void) {
    stop_requested = CreateEventW(NULL, TRUE, FALSE, NULL);
    stop_ready = CreateEventW(NULL, TRUE, FALSE, NULL);
    if (!stop_requested || !stop_ready) return 0;
    HANDLE worker = CreateThread(NULL, 0, stop_worker, NULL, 0, NULL);
    if (!worker) return 0;
    CloseHandle(worker);
    HANDLE messages = CreateThread(NULL, 0, stop_messages, NULL, 0, NULL);
    if (!messages) return 0;
    CloseHandle(messages);
    return WaitForSingleObject(stop_ready, STOP_WAIT_MS) == WAIT_OBJECT_0;
}
#endif
