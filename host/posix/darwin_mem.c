/* macOS memory, written against the SDK's mach_vm.h, vm_statistics.h and mmap(2).
   MADV_DONTNEED/MADV_FREE do not promise zero on Darwin. MAP_FIXED is used only
   to replace complete host pages inside a caller-owned, checked writable range. */
#if defined(__APPLE__)
#include "darwin_mem.h"
#include <errno.h>
#include <mach/mach.h>
#include <mach/mach_vm.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

static int mach_errno(kern_return_t result) {
    switch (result) {
    case KERN_NO_SPACE: case KERN_RESOURCE_SHORTAGE: errno = ENOMEM; break;
    case KERN_PROTECTION_FAILURE: errno = EACCES; break;
    default: errno = EINVAL; break;
    }
    return -1;
}

static int range_end(void *addr, size_t size, uintptr_t *end) {
    if (!addr || !size || size > UINTPTR_MAX - (uintptr_t)addr) { errno = EINVAL; return -1; }
    *end = (uintptr_t)addr + size;
    return 0;
}

void *darwin_mem_map(void *addr, size_t size, int protect) {
    const uintptr_t mask = (uintptr_t)getpagesize() - 1;
    if (!size || ((uintptr_t)addr & mask) || size > UINTPTR_MAX - mask ||
        ((size + mask) & ~mask) > UINTPTR_MAX - (uintptr_t)addr) {
        errno = EINVAL;
        return MAP_FAILED;
    }
    mach_vm_address_t at = (mach_vm_address_t)addr;
    const kern_return_t result = mach_vm_map(mach_task_self(), &at, size, 0,
        addr ? VM_FLAGS_FIXED : VM_FLAGS_ANYWHERE, MACH_PORT_NULL, 0, FALSE,
        protect, VM_PROT_ALL, VM_INHERIT_COPY);
    if (result != KERN_SUCCESS) {
        mach_errno(result);
        if (addr && result == KERN_NO_SPACE) errno = EEXIST;
        return MAP_FAILED;
    }
    return (void *)(uintptr_t)at;
}

/* Fail before changing any bytes if a request crosses a hole or a non-writable
   mapping. This does not replace the caller's ownership/lifetime requirement. */
static int writable_range(uintptr_t lo, uintptr_t hi) {
    while (lo < hi) {
        mach_vm_address_t at = lo;
        mach_vm_size_t size = 0;
        vm_region_basic_info_data_64_t info;
        mach_msg_type_number_t count = VM_REGION_BASIC_INFO_COUNT_64;
        mach_port_t object = MACH_PORT_NULL;
        const kern_return_t result = mach_vm_region(mach_task_self(), &at, &size,
            VM_REGION_BASIC_INFO_64, (vm_region_info_t)&info, &count, &object);
        if (object != MACH_PORT_NULL) mach_port_deallocate(mach_task_self(), object);
        if (result != KERN_SUCCESS) return mach_errno(result);
        if (at > lo || !size || size > UINTPTR_MAX - at) { errno = EINVAL; return -1; }
        if ((info.protection & (VM_PROT_READ | VM_PROT_WRITE)) != (VM_PROT_READ | VM_PROT_WRITE)) {
            errno = EACCES;
            return -1;
        }
        lo = at + size;
    }
    return 0;
}

int darwin_mem_discard(void *addr, size_t size) {
    if (!size) return 0;
    uintptr_t end;
    if (range_end(addr, size, &end) || writable_range((uintptr_t)addr, end)) return -1;
    const uintptr_t start = (uintptr_t)addr, mask = (uintptr_t)getpagesize() - 1;
    const size_t edge = (size_t)(-start & mask);
    if (size <= edge) { memset(addr, 0, size); return 0; }
    const uintptr_t lo = start + edge, hi = end & ~mask;
    /* One replacement operation, not deallocate followed by an allocation another
       thread could beat us to. On failure, don't report a successful discard. */
    if (hi > lo && mmap((void *)lo, hi - lo, PROT_READ | PROT_WRITE,
                       MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0) == MAP_FAILED) return -1;
    if (edge) memset(addr, 0, edge);
    if (hi < end) memset((void *)hi, 0, end - hi);
    return 0;
}

int darwin_mem_protect(void *addr, size_t size, int protect) {
    uintptr_t end;
    if (range_end(addr, size, &end)) return -1;
    const uintptr_t mask = (uintptr_t)getpagesize() - 1;
    if (end > UINTPTR_MAX - mask) { errno = EINVAL; return -1; }
    const uintptr_t lo = (uintptr_t)addr & ~mask, hi = (end + mask) & ~mask;
    return mprotect((void *)lo, hi - lo, protect);
}
#endif
