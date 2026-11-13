/* (longshot) The crash window (docs/crash-report-window.md): host/hos_crash_window.c */
#pragma once

/* At startup, once the log is open: the window's command, from the game folder's longshot_version.txt */
void hos_crash_window_init(void);
/* The same for a given folder (the tests: tests/test_crash_window.py) */
void hos_crash_window_init_in(const char *folder);
/* At a crash nothing handles, after the CRASH: lines are flushed (report_crash, host/mkw_glue.c): crash_pending.txt, and the
   window started. Once per process; nothing when init found no builder or the crash window is off */
void hos_crash_window_open(void);
