/* Darwin arm64 host nonlocal jumps, not guest register or fiber contexts.

   AAPCS64: x19-x29 and SP, and only the low 64 bits of v8-v15, are callee-saved:
   https://github.com/ARM-software/abi-aa/blob/2025Q1/aapcs64/aapcs64.rst
   Apple reserves x18 and requires x29 to address a valid frame record:
   https://developer.apple.com/documentation/xcode/writing-arm64-code-for-apple-platforms

   These leaf routines never touch x18 or create a frame. Saving x29 itself, not a computed frame address,
   keeps the caller's frame chain and spill slots intact. FPCR and FPSR are restored too, matching the x86
   jump's floating-point environment snapshot (FPSR need not be preserved by an ordinary AAPCS64 call).
   The target must be a still-live frame on the current stack (a thread's stack or its current fiber).
   It saves no signal mask, TLS or fiber ownership; darwin_fiber.c owns scheduling and nonlocal-slot lifetime.

   buf is the existing 32-pointer slot, aligned to 8 bytes: x19-x28 at 0-72, x29/x30 at 80/88, SP at 96,
   d8-d15 at 112-168, FPCR/FPSR at 176/184. No access needs 16-byte buffer alignment. SP stays 16-byte aligned. */
#if defined(__APPLE__)
#include "darwin_cpu.h"
#if defined(__aarch64__)
#if defined(__arm64e__)
/* arm64e needs an authenticated SP/FP/LR snapshot, not a raw saved return address. Do not silently weaken it.
   https://clang.llvm.org/docs/PointerAuthentication.html#register-clobbering */
#error "Darwin host jumps support arm64, not arm64e authenticated contexts"
#endif

__attribute__((naked, returns_twice)) int recomp_sj_setjmp(void **buf) {
    __asm__ volatile(
        "stp x19, x20, [x0, #0]\n\t"
        "stp x21, x22, [x0, #16]\n\t"
        "stp x23, x24, [x0, #32]\n\t"
        "stp x25, x26, [x0, #48]\n\t"
        "stp x27, x28, [x0, #64]\n\t"
        "stp x29, x30, [x0, #80]\n\t"
        "mov x9, sp\n\t"
        "str x9, [x0, #96]\n\t"
        "stp d8, d9, [x0, #112]\n\t"
        "stp d10, d11, [x0, #128]\n\t"
        "stp d12, d13, [x0, #144]\n\t"
        "stp d14, d15, [x0, #160]\n\t"
        "mrs x9, fpcr\n\t"
        "mrs x10, fpsr\n\t"
        "stp x9, x10, [x0, #176]\n\t"
        "mov w0, #0\n\t"
        "ret\n\t");
}

__attribute__((naked, noreturn)) void recomp_sj_longjmp(void **buf, int value) {
    __asm__ volatile(
        "ldp x19, x20, [x0, #0]\n\t"
        "ldp x21, x22, [x0, #16]\n\t"
        "ldp x23, x24, [x0, #32]\n\t"
        "ldp x25, x26, [x0, #48]\n\t"
        "ldp x27, x28, [x0, #64]\n\t"
        "ldp x29, x30, [x0, #80]\n\t"
        "ldr x9, [x0, #96]\n\t"
        "ldp d8, d9, [x0, #112]\n\t"
        "ldp d10, d11, [x0, #128]\n\t"
        "ldp d12, d13, [x0, #144]\n\t"
        "ldp d14, d15, [x0, #160]\n\t"
        "ldp x10, x11, [x0, #176]\n\t"
        "msr fpcr, x10\n\t"
        "msr fpsr, x11\n\t"
        "cmp w1, #0\n\t"
        "cinc w0, w1, eq\n\t"
        "mov sp, x9\n\t"
        "ret\n\t");
}
#endif
#endif
