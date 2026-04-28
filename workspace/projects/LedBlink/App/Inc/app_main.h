/**
 * @file    app_main.h
 * @brief   Application layer for LedBlink project
 *
 * Blinks the onboard LED (PC13) at 1Hz using bare-metal register access.
 * System clock configured to HSI 8MHz, SysTick provides 1ms timing base.
 *
 * @note    This file is part of the application layer and is safe to modify.
 *          CubeMX-generated files under Core/ and Drivers/ are NOT modified.
 */

#ifndef APP_MAIN_H
#define APP_MAIN_H

#include <stdint.h>

/**
 * @brief   Initialize system clock, GPIO, and SysTick timer.
 *
 * Configures:
 *   - HSI as system clock source (8 MHz)
 *   - GPIOC clock enabled via RCC
 *   - PC13 as push-pull output, 50 MHz speed
 *   - SysTick for 1ms interrupt (counts down from 8000-1 at 8 MHz)
 *
 * @note    Call once at startup before entering the main loop.
 */
int app_main_init(void);

/**
 * @brief   Main application loop.
 *
 * Toggles PC13 every 500ms to achieve 1Hz blink rate.
 * Uses blocking delay based on SysTick counter.
 * Never returns.
 */
void app_main_loop(void);

#endif /* APP_MAIN_H */
