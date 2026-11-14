/* Authored native register fixture. This frame is suspended on one pthread and resumed on another.
   The caller supplies 18 distinct bit patterns and an output with offsets asserted in C. */
.text
.globl _darwin_fiber_registers
.p2align 2
_darwin_fiber_registers:
#if defined(__aarch64__)
    stp x29, x30, [sp, #-16]!
    mov x29, sp
    sub sp, sp, #176
    stp x19, x20, [sp, #0]
    stp x21, x22, [sp, #16]
    stp x23, x24, [sp, #32]
    stp x25, x26, [sp, #48]
    stp x27, x28, [sp, #64]
    stp d8, d9, [sp, #80]
    stp d10, d11, [sp, #96]
    stp d12, d13, [sp, #112]
    stp d14, d15, [sp, #128]
    mrs x9, fpcr
    mrs x10, fpsr
    stp x9, x10, [sp, #144]
    str x2, [sp, #160]
    eor x9, x9, #0x400000
    eor x10, x10, #0x8000000
    msr fpcr, x9
    msr fpsr, x10
    str x9, [x2, #176]
    str x10, [x2, #192]
    str x29, [x2, #144]
    mov x9, sp
    str x9, [x2, #160]
    ldp x19, x20, [x1, #0]
    ldp x21, x22, [x1, #16]
    ldp x23, x24, [x1, #32]
    ldp x25, x26, [x1, #48]
    ldp x27, x28, [x1, #64]
    ldp d8, d9, [x1, #80]
    ldp d10, d11, [x1, #96]
    ldp d12, d13, [x1, #112]
    ldp d14, d15, [x1, #128]
    bl _SwitchToFiber
    ldr x2, [sp, #160]
    stp x19, x20, [x2, #0]
    stp x21, x22, [x2, #16]
    stp x23, x24, [x2, #32]
    stp x25, x26, [x2, #48]
    stp x27, x28, [x2, #64]
    stp d8, d9, [x2, #80]
    stp d10, d11, [x2, #96]
    stp d12, d13, [x2, #112]
    stp d14, d15, [x2, #128]
    str x29, [x2, #152]
    mov x9, sp
    str x9, [x2, #168]
    mrs x9, fpcr
    mrs x10, fpsr
    str x9, [x2, #184]
    str x10, [x2, #200]
    ldp x9, x10, [sp, #144]
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
    add sp, sp, #176
    ldp x29, x30, [sp], #16
    ret
#elif defined(__x86_64__)
    pushq %rbp
    movq %rsp, %rbp
    subq $80, %rsp
    movq %rbx, 0(%rsp)
    movq %r12, 8(%rsp)
    movq %r13, 16(%rsp)
    movq %r14, 24(%rsp)
    movq %r15, 32(%rsp)
    movq %rdx, 48(%rsp)
    stmxcsr 64(%rsp)
    fnstcw 68(%rsp)
    movl 64(%rsp), %eax
    xorl $0x2000, %eax
    movl %eax, 72(%rsp)
    ldmxcsr 72(%rsp)
    movq %rax, 176(%rdx)
    movzwl 68(%rsp), %eax
    xorl $0x400, %eax
    movw %ax, 70(%rsp)
    fldcw 70(%rsp)
    movq %rax, 192(%rdx)
    movq %rbp, 144(%rdx)
    movq %rsp, 160(%rdx)
    movq 0(%rsi), %rbx
    movq 8(%rsi), %r12
    movq 16(%rsi), %r13
    movq 24(%rsi), %r14
    movq 32(%rsi), %r15
    callq _SwitchToFiber
    movq 48(%rsp), %rdx
    movq %rbx, 0(%rdx)
    movq %r12, 8(%rdx)
    movq %r13, 16(%rdx)
    movq %r14, 24(%rdx)
    movq %r15, 32(%rdx)
    movq %rbp, 152(%rdx)
    movq %rsp, 168(%rdx)
    stmxcsr 72(%rsp)
    fnstcw 70(%rsp)
    movl 72(%rsp), %eax
    movq %rax, 184(%rdx)
    movzwl 70(%rsp), %eax
    movq %rax, 200(%rdx)
    ldmxcsr 64(%rsp)
    fldcw 68(%rsp)
    movq 0(%rsp), %rbx
    movq 8(%rsp), %r12
    movq 16(%rsp), %r13
    movq 24(%rsp), %r14
    movq 32(%rsp), %r15
    leave
    retq
#else
#error "fixture requires arm64 or x86_64"
#endif
