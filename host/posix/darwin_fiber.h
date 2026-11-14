/* SPDX-License-Identifier: GPL-3.0-or-later */
/* Stackful Darwin fibers. Native TLS belongs to the running pthread, not to a saved context. */
#pragma once
#if defined(__APPLE__)
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
void *darwin_fiber_convert(void *param, void (*thread_exit)(uint32_t));
int darwin_fiber_unconvert(void);
void *darwin_fiber_create(size_t size, void (*entry)(void *), void *param);
int darwin_fiber_switch(void *fiber);
int darwin_fiber_delete(void *fiber);
void *darwin_fiber_current(void);
void *darwin_fiber_data(void);
/* One calloc-owned runtime slot (size 0 only queries). It follows a fiber across pthreads, or the pthread
   until conversion. Unconversion transfers the root's slot back; deletion/exit frees it. */
void *darwin_fiber_local(size_t size);
/* Called by the Win32 ExitThread wrapper before native thread teardown. If on a user stack, resumes the
   current pthread's root first, so neither libpthread nor cleanup runs on a stack being unmapped. */
void darwin_fiber_before_thread_exit(uint32_t code);
#ifdef __cplusplus
}
#endif
#endif
