/* macOS host-page operations. Guest addresses and dirty marks still use 4 KiB pages. */
#ifndef LONGSHOT_DARWIN_MEM_H
#define LONGSHOT_DARWIN_MEM_H

#include <stddef.h>

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

#ifdef __cplusplus
}
#endif
#endif
