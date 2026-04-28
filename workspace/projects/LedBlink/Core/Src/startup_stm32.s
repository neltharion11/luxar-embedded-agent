.syntax unified
.cpu cortex-m3
.thumb

.global g_pfnVectors
.global Reset_Handler
.global Default_Handler
.global SysTick_Handler
.type Reset_Handler, %function
.type Default_Handler, %function
.type SysTick_Handler, %function
.thumb_func

.section .isr_vector, "a", %progbits
g_pfnVectors:
    .word _estack
    .word Reset_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word Default_Handler
    .word 0
    .word 0
    .word 0
    .word 0
    .word Default_Handler
    .word Default_Handler
    .word 0
    .word Default_Handler
    .word SysTick_Handler

.section .text.Reset_Handler, "ax", %progbits
Reset_Handler:
    ldr r0, =_sidata
    ldr r1, =_sdata
    ldr r2, =_edata
1:
    cmp r1, r2
    bcs 2f
    ldr r3, [r0], #4
    str r3, [r1], #4
    b 1b
2:
    ldr r1, =_sbss
    ldr r2, =_ebss
    movs r3, #0
3:
    cmp r1, r2
    bcs 4f
    str r3, [r1], #4
    b 3b
4:
    bl SystemInit
    bl main
5:
    b 5b

.section .text.Default_Handler, "ax", %progbits
Default_Handler:
6:
    b 6b

.weak SysTick_Handler
.section .text.SysTick_Handler, "ax", %progbits
SysTick_Handler:
    b .
