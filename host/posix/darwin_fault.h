/* Darwin's signal context is pointer-valued, unlike Linux's. No thread suspension API here. */
#ifndef LONGSHOT_DARWIN_FAULT_H
#define LONGSHOT_DARWIN_FAULT_H
#if defined(__APPLE__)
#include "windows.h"
#include <signal.h>
#include <sys/ucontext.h>

#ifdef __cplusplus
extern "C" {
#endif
/* The x64-shaped bridge keeps its layout: arm64 uses only Rip (PC) and Rsp (SP).
   Its x64 integer, EFlags and debug slots are zero, not aliases for ARM registers.
   All other native state stays in the signal frame, untouched on return. */
void darwin_fault_context_from(CONTEXT *c, const ucontext_t *uc);
void darwin_fault_context_to(ucontext_t *uc, const CONTEXT *c);
/* Windows access kind: 0 read, 1 write, 8 execute. Other syndromes are not writes. */
ULONG_PTR darwin_fault_access(const ucontext_t *uc);
/* Keep faults in a callback from recursively entering the exception bridge. Uses
   an existing alternate stack when the thread has one; does not allocate a stack. */
void darwin_fault_action(struct sigaction *sa);
#ifdef __cplusplus
}
#endif
#endif
#endif
