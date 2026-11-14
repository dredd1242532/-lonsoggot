/* macOS host-page operations. Guest addresses and dirty marks still use 4 KiB pages. */
#ifndef LONGSHOT_DARWIN_MEM_H
#define LONGSHOT_DARWIN_MEM_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* mmap-style result and errno. A non-NULL address must be host-page aligned;
   an occupied range is refused, never replaced (EEXIST). */
void *darwin_mem_map(void *addr, size_t size, int protect);

/* Zero exactly [addr, addr + size), returning complete interior host pages to the OS.
   The caller owns writable anonymous memory and serializes its lifetime. Imported,
   shared or file-backed memory must instead be zeroed in place by its owner.
   Partial host pages keep their backing and every byte outside the range.
   Returns 0 on success, -1 with errno on failure. No unmap/remap gap is opened. */
int darwin_mem_discard(void *addr, size_t size);

/* Change protection at the real host granule (16 KiB on native Apple Silicon,
   4 KiB in an x86-64 process). Not independent guest-subpage protection. */
int darwin_mem_protect(void *addr, size_t size, int protect);

/* The kernel's real mapping (or hole up to the next mapping), not a guessed
   guest-window permission. base/end and protect use the host page granule. */
typedef struct {
    uintptr_t base, end;
    int mapped, protect, max_protect, shared;
} DarwinMemInfo;
int darwin_mem_query(const void *addr, DarwinMemInfo *info);

/* Executable-owned guest services, implemented ONLY in darwin_guest_mem.c.
   The shared Win32 shim resolves these from RTLD_MAIN_ONLY. No private ledger
   or fallback is allowed in a renderer/helper image. The reservation is RW and
   demand-paged; commit validates coverage without replacing/clearing backing. */
void *hos_posix_guest_reserve(void *addr, size_t size);
int hos_posix_guest_commit(void *addr, size_t size);
int hos_posix_guest_discard(void *addr, size_t size);
int hos_posix_guest_protect(void *addr, size_t size, int protect, int *old);

/* Guest offsets [lo, hi), covering the FULL outward-rounded imported backing.
   Pin returns 1 only after remapping/discard is excluded, 0 on failure. Every
   successful pin (duplicates included) needs one matching unpin after the last
   active/retired Vulkan memory owner is destroyed following GPU completion.
   Pin preserves backing identity, not bytes: ordinary frees may zero in place. */
int hos_posix_guest_import_pin(uint64_t lo, uint64_t hi);
void hos_posix_guest_import_unpin(uint64_t lo, uint64_t hi);

/* Optional diagnostic watches cover WHOLE native pages. The fault path takes
   no ownership mutex, calls no renderer, allocates nothing, and marks every
   affected 4 KiB dirty index. 1 = handled, 0 = not a watch, -1 = recovery failed.
   Writable cancellation still acknowledges pending diagnostic faults; explicit
   RO/NOACCESS never becomes writable through that acknowledgement. Failed
   whole-range protection preserves prior watch states. Watched ranges remain
   conservatively dirty even while disarmed/after an error. */
int hos_posix_guest_watch(const void *addr, size_t size);
int hos_posix_guest_watch_fault(const void *addr);
int hos_posix_guest_watch_rearm(void);
void hos_posix_guest_watch_mark(void);

#ifdef __cplusplus
}
#endif
#endif
