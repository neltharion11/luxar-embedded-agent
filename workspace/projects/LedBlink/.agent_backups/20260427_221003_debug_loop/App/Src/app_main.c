/**
 * @file    app_main.c
 * @brief   Application layer implementation for LedBlink
 *
 * Bare-metal register programming for STM32F103C8T6.
 * No HAL functions used — direct register access per RM0008 reference manual.
 *
 * @note    Register addresses and bit definitions are for STM32F1 family.
 *          Verify against target MCU reference manual before production use.
 *
 * @warning This code is tightly coupled with the STM32F1 series. For portability,
 *          consider abstracting hardware access behind a HAL or hardware abstraction layer.
 */

#include "app_main.h"

/* CMSIS-compatible inline intrinsics for ARM Cortex-M */
#define __disable_irq()  __asm__ volatile ("cpsid i" ::: "memory")
#define __enable_irq()   __asm__ volatile ("cpsie i" ::: "memory")
#define __DSB()           __asm__ volatile ("dsb 0" ::: "memory")
#define __ISB()           __asm__ volatile ("isb 0" ::: "memory")

/* ========================================================================== */
/* Register Definitions (STM32F103C8T6, RM0008 reference)                     */
/* ========================================================================== */

/* Peripheral base addresses */
#define PERIPH_BASE         0x40000000UL
#define APB2PERIPH_BASE     (PERIPH_BASE + 0x10000UL)
#define AHBPERIPH_BASE      (PERIPH_BASE + 0x20000UL)

/* RCC registers (base: 0x40021000) */
#define RCC_BASE            (AHBPERIPH_BASE + 0x1000UL)
#define RCC_APB2ENR         (*(volatile uint32_t *)(RCC_BASE + 0x18))
#define RCC_CR              (*(volatile uint32_t *)(RCC_BASE + 0x00))
#define RCC_CFGR            (*(volatile uint32_t *)(RCC_BASE + 0x04))

/* RCC bit definitions */
#define RCC_APB2ENR_IOPCEN  (1UL << 4)   /* GPIOC clock enable */
#define RCC_CR_HSION        (1UL << 0)   /* HSI oscillator enable */
#define RCC_CR_HSIRDY       (1UL << 1)   /* HSI ready flag */
#define RCC_CFGR_SW_Msk     (0x3UL << 0) /* System clock switch mask (bits 1:0) */
#define RCC_CFGR_SW_HSI     (0x0UL << 0) /* System clock = HSI (SW bits = 00) */
#define RCC_CFGR_SWS_Msk    (0x3UL << 2) /* System clock switch status mask (bits 3:2) */
#define RCC_CFGR_SWS_HSI    (0x0UL << 2) /* System clock source = HSI (SWS bits = 00) */

/* GPIOC registers (base: 0x40011000) */
#define GPIOC_BASE          (APB2PERIPH_BASE + 0x1000UL)
#define GPIOC_CRH           (*(volatile uint32_t *)(GPIOC_BASE + 0x04))
#define GPIOC_ODR           (*(volatile uint32_t *)(GPIOC_BASE + 0x0C))
#define GPIOC_BSRR          (*(volatile uint32_t *)(GPIOC_BASE + 0x10))
#define GPIOC_BRR           (*(volatile uint32_t *)(GPIOC_BASE + 0x14))

/* PC13 is in high register (CRH), bits 20-23 */
#define GPIOC_CRH_CNF13_OFFSET  22      /* 2 bits for CNF13 */
#define GPIOC_CRH_MODE13_OFFSET 20      /* 2 bits for MODE13 */
#define GPIOC_CRH_MODE13_MASK   (0x3UL << GPIOC_CRH_MODE13_OFFSET)
#define GPIOC_CRH_CNF13_MASK    (0x3UL << GPIOC_CRH_CNF13_OFFSET)

/* MODE13 = 11 (output 50 MHz), CNF13 = 00 (push-pull) */
#define GPIOC_CRH_MODE13_50MHZ  (0x3UL << GPIOC_CRH_MODE13_OFFSET)
#define GPIOC_CRH_CNF13_PP      (0x0UL << GPIOC_CRH_CNF13_OFFSET)

/* SysTick registers (base: 0xE000E010) */
#define SYSTICK_BASE        0xE000E010UL
#define STK_CTRL            (*(volatile uint32_t *)(SYSTICK_BASE + 0x00))
#define STK_LOAD            (*(volatile uint32_t *)(SYSTICK_BASE + 0x04))
#define STK_VAL             (*(volatile uint32_t *)(SYSTICK_BASE + 0x08))

/* SysTick control bits */
#define STK_CTRL_ENABLE     (1UL << 0)   /* Counter enable */
#define STK_CTRL_TICKINT    (1UL << 1)   /* Interrupt enable */
#define STK_CTRL_CLKSOURCE  (1UL << 2)   /* Clock source: processor clock */
#define STK_CTRL_COUNTFLAG  (1UL << 16)  /* Count-to-zero flag */

/* ========================================================================== */
/* Timeout Definitions                                                        */
/* ========================================================================== */

/**
 * @brief   Maximum number of iterations to wait for a hardware flag.
 *
 * This is a safe upper bound to prevent infinite loops in case of hardware
 * malfunction. The value is chosen to be large enough for typical 8 MHz HSI
 * startup times (a few microseconds) while providing a generous margin.
 */
#define HSI_TIMEOUT_MAX     1000000UL

/* ========================================================================== */
/* Global State                                                               */
/* ========================================================================== */

/**
 * @brief   Volatile tick counter incremented by SysTick interrupt.
 *
 * Used for blocking delay implementation.
 * Declared volatile to prevent compiler optimization in delay loops.
 *
 * @note    On Cortex-M3, read-modify-write of a 32-bit variable is atomic.
 *          The volatile qualifier ensures the compiler reads from memory
 *          each time, preventing infinite loops due to register caching.
 *
 * @warning Access to this variable from both interrupt and main context
 *          requires atomic read protection. Use @ref sys_tick_get() for
 *          safe access.
 */
static volatile uint32_t g_sys_tick_count = 0;

/* ========================================================================== */
/* SysTick Interrupt Handler                                                  */
/* ========================================================================== */

/**
 * @brief   SysTick interrupt handler.
 *
 * Increments the global tick counter every 1ms.
 * Called automatically by the Cortex-M3 core when SysTick reaches zero.
 *
 * @note    This handler is placed in the interrupt vector table by the linker.
 *          Ensure startup_stm32f103x6.s or similar links SysTick_Handler to
 *          this symbol. If using CubeMX startup file, this function name
 *          must match the vector table entry.
 *
 * @warning This function runs in interrupt context. Keep it short and avoid
 *          calling any functions that might block or use the same resources.
 */
void SysTick_Handler(void)
{
    g_sys_tick_count++;
}

/* ========================================================================== */
/* Private Helper Functions                                                   */
/* ========================================================================== */

/**
 * @brief   Safely read the current system tick count.
 *
 * Uses a critical section to ensure atomic read of the tick counter
 * that is modified in interrupt context. This prevents reading a
 * corrupted value if the interrupt fires during the read operation.
 *
 * @return  Current tick count (milliseconds since SysTick initialization).
 */
static inline uint32_t sys_tick_get(void)
{
    uint32_t tick;

    /* Disable interrupts to ensure atomic read */
    __disable_irq();
    tick = g_sys_tick_count;
    __enable_irq();

    return tick;
}

/**
 * @brief   Blocking delay in milliseconds.
 *
 * Busy-waits using the SysTick counter.
 * Accurate only if SysTick is configured for 1ms interrupts.
 *
 * @warning This is a blocking delay. The CPU is 100% occupied during the wait.
 *          Do not use in time-critical or power-sensitive applications.
 *          Consider using a non-blocking state machine or RTOS delay instead.
 *
 * @param   ms  Number of milliseconds to wait.
 */
static void delay_ms(uint32_t ms)
{
    uint32_t start = sys_tick_get();

    /* Wait until the required number of ticks have elapsed */
    /* Use int32_t subtraction to handle counter wraparound correctly */
    while ((int32_t)(sys_tick_get() - start) < (int32_t)ms)
    {
        /* Wait — no operation needed */
    }
}

/* ========================================================================== */
/* Public API Implementation                                                  */
/* ========================================================================== */

/**
 * @brief   Initializes the system for LED blinking.
 *
 * Configures the system clock to HSI, enables the GPIOC peripheral clock,
 * configures PC13 as a push-pull output, sets up SysTick for 1ms interrupts,
 * and ensures the LED starts in the OFF state.
 *
 * @note    This function must be called once before entering the main loop.
 *          It assumes no other initialization has been performed.
 *
 * @warning This function contains busy-wait loops with timeouts. If a timeout
 *          expires, the system may be in an undefined state. In a production
 *          system, consider implementing a more robust error handling strategy
 *          (e.g., reset, fallback to a safe mode, or report error).
 *
 * @return  0 on success, -1 if HSI failed to start, -2 if clock switch failed.
 */
int app_main_init(void)
{
    uint32_t timeout;

    /* ---------------------------------------------------------------------- */
    /* Step 1: Configure system clock (HSI 8 MHz)                             */
    /* ---------------------------------------------------------------------- */

    /* Enable HSI oscillator */
    RCC_CR |= RCC_CR_HSION;

    /* Wait for HSI to stabilize, with timeout */
    timeout = HSI_TIMEOUT_MAX;
    while (!(RCC_CR & RCC_CR_HSIRDY))
    {
        if (--timeout == 0)
        {
            /* HSI failed to start. Return error. */
            return -1;
        }
    }

    /* Select HSI as system clock source */
    /* Clear SW bits (bits 1:0) and set to HSI (0b00) */
    RCC_CFGR = (RCC_CFGR & ~RCC_CFGR_SW_Msk) | RCC_CFGR_SW_HSI;

    /* Wait for clock switch to complete, with timeout */
    /* Check SWS bits (bits 3:2) to confirm HSI is active */
    timeout = HSI_TIMEOUT_MAX;
    while ((RCC_CFGR & RCC_CFGR_SWS_Msk) != RCC_CFGR_SWS_HSI)
    {
        if (--timeout == 0)
        {
            /* Clock switch failed. Return error. */
            return -2;
        }
    }

    /* ---------------------------------------------------------------------- */
    /* Step 2: Enable GPIOC peripheral clock                                  */
    /* ---------------------------------------------------------------------- */

    RCC_APB2ENR |= RCC_APB2ENR_IOPCEN;

    /* Memory barrier: ensure clock enable write completes before accessing GPIOC */
    __DSB();

    /* ---------------------------------------------------------------------- */
    /* Step 3: Configure PC13 as push-pull output, 50 MHz speed               */
    /* ---------------------------------------------------------------------- */

    /* Clear existing MODE13 and CNF13 bits */
    GPIOC_CRH &= ~(GPIOC_CRH_MODE13_MASK | GPIOC_CRH_CNF13_MASK);

    /* Set MODE13 = 11 (output 50 MHz), CNF13 = 00 (push-pull) */
    GPIOC_CRH |= (GPIOC_CRH_MODE13_50MHZ | GPIOC_CRH_CNF13_PP);

    /* ---------------------------------------------------------------------- */
    /* Step 4: Configure SysTick for 1ms interrupts                           */
    /* ---------------------------------------------------------------------- */

    /* Disable SysTick during configuration */
    STK_CTRL = 0;

    /*
     * Reload value for 1ms at 8 MHz: (8000000 / 1000) - 1 = 7999
     *
     * @note    HSI accuracy is typically ±1%. The actual SysTick frequency
     *          may deviate slightly from 1 kHz, causing minor timing errors.
     *          For precise timing, use an external crystal oscillator or
     *          calibrate the HSI using the RTC or other reference.
     */
    STK_LOAD = 7999UL;

    /* Clear current value */
    STK_VAL = 0;

    /* Enable SysTick: processor clock, interrupt enabled, counter enabled */
    STK_CTRL = STK_CTRL_ENABLE | STK_CTRL_TICKINT | STK_CTRL_CLKSOURCE;

    /* ---------------------------------------------------------------------- */
    /* Step 5: Ensure LED starts in OFF state (PC13 high)                     */
    /* ---------------------------------------------------------------------- */

    /* Set PC13 high (LED off — active low on most STM32F103 boards) */
    /* Using BSRR high half-word for reset (bit 13 + 16) */
    GPIOC_BSRR = (1UL << (13 + 16));

    return 0;
}

/**
 * @brief   Main application loop.
 *
 * Continuously toggles the LED on PC13 with a 500ms delay.
 * The LED is active-low, so setting the pin low turns it on,
 * and setting it high turns it off.
 *
 * @note    This function never returns.
 */
void app_main_loop(void)
{
    while (1)
    {
        /* Turn LED ON: set PC13 low (active low) */
        /* Using BSRR high half-word for reset (bit 13 + 16) */
        GPIOC_BSRR = (1UL << (13 + 16));

        /* Wait 500ms */
        delay_ms(500);

        /* Turn LED OFF: set PC13 high (active low) */
        /* Using BSRR low half-word for set (bit 13) */
        GPIOC_BSRR = (1UL << 13);

        /* Wait 500ms */
        delay_ms(500);
    }
}
