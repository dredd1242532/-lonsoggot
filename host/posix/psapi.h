/* (linux) psapi: the process's memory counters, from /proc/self/status (the working set is the resident set, private
   memory the anonymous part of it), for the back end's memory log lines */
#ifndef LONGSHOT_POSIX_PSAPI_H
#define LONGSHOT_POSIX_PSAPI_H
#include <windows.h>
#if defined(__APPLE__)
#include <mach/mach.h>
#include <sys/resource.h>
#endif
typedef struct {
    DWORD cb, PageFaultCount;
    SIZE_T PeakWorkingSetSize, WorkingSetSize, QuotaPeakPagedPoolUsage, QuotaPagedPoolUsage, QuotaPeakNonPagedPoolUsage,
        QuotaNonPagedPoolUsage, PagefileUsage, PeakPagefileUsage;
} PROCESS_MEMORY_COUNTERS;
typedef struct {
    DWORD cb, PageFaultCount;
    SIZE_T PeakWorkingSetSize, WorkingSetSize, QuotaPeakPagedPoolUsage, QuotaPagedPoolUsage, QuotaPeakNonPagedPoolUsage,
        QuotaNonPagedPoolUsage, PagefileUsage, PeakPagefileUsage, PrivateUsage;
} PROCESS_MEMORY_COUNTERS_EX;
static inline BOOL K32GetProcessMemoryInfo(HANDLE p, PROCESS_MEMORY_COUNTERS *out, DWORD cb) {
#if defined(__APPLE__)
    if (!out || cb < sizeof *out) { SetLastError(ERROR_INVALID_PARAMETER); return FALSE; }
    if (p && p != GetCurrentProcess()) { SetLastError(ERROR_NOT_SUPPORTED); return FALSE; }
    mach_task_basic_info_data_t basic;
    mach_msg_type_number_t count = MACH_TASK_BASIC_INFO_COUNT;
    if (task_info(mach_task_self(), MACH_TASK_BASIC_INFO, (task_info_t)&basic, &count) != KERN_SUCCESS) {
        SetLastError(ERROR_INVALID_HANDLE);
        return FALSE;
    }
    task_vm_info_data_t vm;
    count = TASK_VM_INFO_COUNT;
    if (task_info(mach_task_self(), TASK_VM_INFO, (task_info_t)&vm, &count) != KERN_SUCCESS) {
        SetLastError(ERROR_NOT_SUPPORTED);
        return FALSE;
    }
    struct rusage usage;
    if (getrusage(RUSAGE_SELF, &usage)) { SetLastError(ERROR_NOT_SUPPORTED); return FALSE; }
    memset(out, 0, cb);
    out->cb = cb;
    out->WorkingSetSize = (SIZE_T)basic.resident_size;
    out->PeakWorkingSetSize = (SIZE_T)usage.ru_maxrss;   /* Darwin reports bytes, not Linux's KiB. */
    const uint64_t faults = (uint64_t)usage.ru_minflt + (uint64_t)usage.ru_majflt;
    out->PageFaultCount = faults > UINT32_MAX ? UINT32_MAX : (DWORD)faults;
    /* The native ledger footprint includes compressed memory; this is not the amount in swap nor
       the 512-GiB sparse address reservation. As on Linux, the unused Win32 quota fields remain zero. */
    out->PagefileUsage = (SIZE_T)vm.phys_footprint;
    if (cb >= sizeof(PROCESS_MEMORY_COUNTERS_EX)) ((PROCESS_MEMORY_COUNTERS_EX *)out)->PrivateUsage = (SIZE_T)vm.phys_footprint;
    return TRUE;
#else
    (void)p;
    FILE *f = fopen("/proc/self/status", "r");
    if (!f) return FALSE;
    SIZE_T rss = 0, hwm = 0, anon = 0;
    char line[256];
    while (fgets(line, sizeof line, f)) {
        unsigned long long kb;
        if (sscanf(line, "VmRSS: %llu", &kb) == 1) rss = (SIZE_T)kb << 10;
        else if (sscanf(line, "VmHWM: %llu", &kb) == 1) hwm = (SIZE_T)kb << 10;
        else if (sscanf(line, "RssAnon: %llu", &kb) == 1) anon = (SIZE_T)kb << 10;
    }
    fclose(f);
    memset(out, 0, cb);
    out->cb = cb, out->WorkingSetSize = rss, out->PeakWorkingSetSize = hwm, out->PagefileUsage = anon;
    if (cb >= sizeof(PROCESS_MEMORY_COUNTERS_EX)) ((PROCESS_MEMORY_COUNTERS_EX *)out)->PrivateUsage = anon;
    return TRUE;
#endif
}
#define GetProcessMemoryInfo K32GetProcessMemoryInfo
#endif
