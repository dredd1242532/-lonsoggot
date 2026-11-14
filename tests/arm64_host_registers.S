/* Game-free register traffic for test_arm64_host.py. All live values, clobbers and snapshots are assembly,
   so the compiler cannot spill them around setjmp. The input patterns and output layout are in arm64_host_test.c.
   The extra frame before hos_setjmp_return makes both SP and FP differ from the saved caller's. */
#if defined(__APPLE__)
.text
.p2align 2
.globl _arm64_host_registers
#if defined(__aarch64__)
/* x0: slot, x1: context, x2: patterns, x3: observed; 208-byte frame, including our caller's frame record. */
_arm64_host_registers:
    sub sp, sp, #208
    stp x19, x20, [sp, #0]
    stp x21, x22, [sp, #16]
    stp x23, x24, [sp, #32]
    stp x25, x26, [sp, #48]
    stp x27, x28, [sp, #64]
    stp d8, d9, [sp, #80]
    stp d10, d11, [sp, #96]
    stp d12, d13, [sp, #112]
    stp d14, d15, [sp, #128]
    stp x0, x1, [sp, #144]
    stp x2, x3, [sp, #160]
    mrs x9, fpcr
    mrs x10, fpsr
    stp x9, x10, [sp, #176]
    stp x29, x30, [sp, #192]
    add x29, sp, #192

    str x29, [x3, #80]
    mov x11, sp
    str x11, [x3, #96]
    str x18, [x3, #112]       /* observe the reserved register; never write it */
    ldr x11, [x29]
    str x11, [x3, #240]
    adr x11, Larm_return
    str x11, [x3, #256]
    ldp x19, x20, [x2, #0]
    ldp x21, x22, [x2, #16]
    ldp x23, x24, [x2, #32]
    ldp x25, x26, [x2, #48]
    ldp x27, x28, [x2, #64]
    ldp d8, d9, [x2, #80]
    ldp d10, d11, [x2, #96]
    ldp d12, d13, [x2, #112]
    ldp d14, d15, [x2, #128]
    eor x9, x9, #0x400000    /* a different, valid rounding mode */
    msr fpcr, x9
    mov x10, #1             /* cumulative invalid-operation flag, no exception raised */
    msr fpsr, x10
    str x9, [x3, #208]
    str x10, [x3, #224]
    bl _recomp_sj_setjmp
Larm_return:
    cbnz w0, Larm_second
    ldr x3, [sp, #168]
    str x0, [x3, #192]
    mov x19, xzr
    mov x20, xzr
    mov x21, xzr
    mov x22, xzr
    mov x23, xzr
    mov x24, xzr
    mov x25, xzr
    mov x26, xzr
    mov x27, xzr
    mov x28, xzr
    movi v8.16b, #0
    movi v9.16b, #0
    movi v10.16b, #0
    movi v11.16b, #0
    movi v12.16b, #0
    movi v13.16b, #0
    movi v14.16b, #0
    movi v15.16b, #0
    mrs x9, fpcr
    eor x9, x9, #0xc00000
    msr fpcr, x9
    msr fpsr, xzr
    ldr x0, [sp, #152]
    mov x1, #0x5004
    sub sp, sp, #32
    stp x29, x30, [sp, #16]
    add x29, sp, #16
    bl _hos_setjmp_return
    /* A failed lookup must be a test failure, not an accidental normal return from this fixture. */
    ldp x29, x30, [sp, #16]
    add sp, sp, #32
    mov w0, #0
Larm_second:
    ldr x3, [sp, #168]
    sxtw x9, w0
    str x9, [x3, #200]
    stp x19, x20, [x3, #0]
    stp x21, x22, [x3, #16]
    stp x23, x24, [x3, #32]
    stp x25, x26, [x3, #48]
    stp x27, x28, [x3, #64]
    str x29, [x3, #88]
    mov x9, sp
    str x9, [x3, #104]
    str x18, [x3, #120]
    stp d8, d9, [x3, #128]
    stp d10, d11, [x3, #144]
    stp d12, d13, [x3, #160]
    stp d14, d15, [x3, #176]
    mrs x9, fpcr
    str x9, [x3, #216]
    mrs x9, fpsr
    str x9, [x3, #232]
    ldr x9, [x29]
    str x9, [x3, #248]
    str x30, [x3, #264]

    ldp x9, x10, [sp, #176]
    msr fpcr, x9
    msr fpsr, x10
    ldp x19, x20, [sp, #0]
    ldp x21, x22, [sp, #16]
    ldp x23, x24, [sp, #32]
    ldp x25, x26, [sp, #48]
    ldp x27, x28, [sp, #64]
    ldp d8, d9, [sp, #80]
    ldp d10, d11, [sp, #96]
    ldp d12, d13, [sp, #112]
    ldp d14, d15, [sp, #128]
    ldp x29, x30, [sp, #192]
    add sp, sp, #208
    ret
#elif defined(__x86_64__)
/* rdi: slot, rsi: context, rdx: patterns, rcx: observed. SysV has no callee-saved vector registers. */
_arm64_host_registers:
    pushq %rbp
    movq %rsp, %rbp
    pushq %rbx
    pushq %r12
    pushq %r13
    pushq %r14
    pushq %r15
    subq $88, %rsp
    movq %rdi, 0(%rsp)
    movq %rsi, 8(%rsp)
    movq %rdx, 16(%rsp)
    movq %rcx, 24(%rsp)
    stmxcsr 32(%rsp)
    fnstcw 36(%rsp)
    movq %rbp, 80(%rcx)
    movq %rsp, 96(%rcx)
    movq (%rbp), %rax
    movq %rax, 240(%rcx)
    movq 0(%rdx), %rbx
    movq 8(%rdx), %r12
    movq 16(%rdx), %r13
    movq 24(%rdx), %r14
    movq 32(%rdx), %r15
    movl 32(%rsp), %eax
    xorl $0x2000, %eax       /* toggle rounding, and set a cumulative flag without raising an exception */
    orl $1, %eax
    movl %eax, 40(%rsp)
    ldmxcsr 40(%rsp)
    movq %rax, 208(%rcx)
    movzwl 36(%rsp), %eax
    xorl $0x400, %eax
    movw %ax, 44(%rsp)
    fldcw 44(%rsp)
    movq %rax, 224(%rcx)
    callq _recomp_sj_setjmp
Lx86_return:
    testl %eax, %eax
    jnz Lx86_second
    movq 24(%rsp), %rcx
    movq %rax, 192(%rcx)
    xorq %rbx, %rbx
    xorq %r12, %r12
    xorq %r13, %r13
    xorq %r14, %r14
    xorq %r15, %r15
    movl 40(%rsp), %eax
    xorl $0x6001, %eax
    movl %eax, 40(%rsp)
    ldmxcsr 40(%rsp)
    movzwl 44(%rsp), %eax
    xorl $0xc00, %eax
    movw %ax, 44(%rsp)
    fldcw 44(%rsp)
    movq 8(%rsp), %rdi
    movl $0x5004, %esi
    pushq %rbp
    movq %rsp, %rbp
    subq $8, %rsp
    callq _hos_setjmp_return
    addq $8, %rsp
    popq %rbp
    xorl %eax, %eax
Lx86_second:
    movq 24(%rsp), %rcx
    movslq %eax, %rax
    movq %rax, 200(%rcx)
    movq %rbx, 0(%rcx)
    movq %r12, 8(%rcx)
    movq %r13, 16(%rcx)
    movq %r14, 24(%rcx)
    movq %r15, 32(%rcx)
    movq %rbp, 88(%rcx)
    movq %rsp, 104(%rcx)
    stmxcsr 40(%rsp)
    movl 40(%rsp), %eax
    movq %rax, 216(%rcx)
    fnstcw 44(%rsp)
    movzwl 44(%rsp), %eax
    movq %rax, 232(%rcx)
    movq (%rbp), %rax
    movq %rax, 248(%rcx)
    ldmxcsr 32(%rsp)
    fldcw 36(%rsp)
    addq $88, %rsp
    popq %r15
    popq %r14
    popq %r13
    popq %r12
    popq %rbx
    popq %rbp
    retq
#endif
#endif
