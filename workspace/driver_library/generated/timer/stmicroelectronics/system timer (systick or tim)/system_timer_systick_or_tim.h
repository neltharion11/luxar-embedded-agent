/**
 * @file    led_blink_timer.h
 * @brief   MCU-independent LED blinking driver using a timer interface.
 * @details This driver controls a single LED to blink at a specified frequency.
 *          It requires a timer interface to be provided via the @ref led_timer_t
 *          structure, abstracting the underlying hardware timer (e.g., SysTick, TIM).
 *          The driver is responsible for managing the blink state and timing logic.
 *
 * @note    This driver is designed to be reusable across different MCU platforms.
 *          It does not use printf, malloc, or free.
 *          All functions return 0 on success, or a negative error code on failure.
 */

#ifndef LED_BLINK_TIMER_H
#define LED_BLINK_TIMER_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief   Maximum supported blink frequency in Hz.
 * @details This is a safety limit to prevent excessively fast blinking that
 *          might not be visible or could stress the hardware.
 */
#define LED_BLINK_TIMER_MAX_FREQUENCY_HZ    10U

/**
 * @brief   Minimum supported blink frequency in Hz.
 * @details A frequency of 0 Hz is considered invalid (LED off or on constantly).
 */
#define LED_BLINK_TIMER_MIN_FREQUENCY_HZ    1U

/**
 * @brief   Error codes for the LED blink timer driver.
 */
typedef enum {
    LED_BLINK_TIMER_OK              =  0, /**< Operation completed successfully. */
    LED_BLINK_TIMER_ERR_NULL_PTR    = -1, /**< A NULL pointer was provided. */
    LED_BLINK_TIMER_ERR_INVALID_FREQ = -2, /**< The requested frequency is out of range. */
    LED_BLINK_TIMER_ERR_TIMER_FAIL  = -3  /**< The underlying timer interface returned an error. */
} led_blink_timer_error_t;

/**
 * @brief   Timer interface structure.
 * @details This structure defines the abstract interface to a hardware timer.
 *          The user must populate these function pointers with platform-specific
 *          implementations before calling @ref led_blink_timer_init.
 *
 *          The timer is expected to be a free-running counter or a periodic
 *          interrupt source. The driver uses it to measure time intervals.
 *
 *          - @ref start: Starts the timer. The timer should begin counting from 0.
 *          - @ref stop: Stops the timer.
 *          - @ref get_ticks: Returns the current tick count of the timer.
 *          - @ref get_tick_frequency_hz: Returns the frequency of the timer ticks in Hz.
 */
typedef struct {
    /**
     * @brief   Starts the hardware timer.
     * @param   context: User-defined context pointer (e.g., timer handle).
     * @return  0 on success, negative error code on failure.
     */
    int (*start)(void *context);

    /**
     * @brief   Stops the hardware timer.
     * @param   context: User-defined context pointer.
     * @return  0 on success, negative error code on failure.
     */
    int (*stop)(void *context);

    /**
     * @brief   Gets the current tick count from the hardware timer.
     * @param   context: User-defined context pointer.
     * @param   ticks: Pointer to store the current tick count.
     * @return  0 on success, negative error code on failure.
     */
    int (*get_ticks)(void *context, uint32_t *ticks);

    /**
     * @brief   Gets the tick frequency of the hardware timer in Hz.
     * @param   context: User-defined context pointer.
     * @param   frequency_hz: Pointer to store the tick frequency.
     * @return  0 on success, negative error code on failure.
     */
    int (*get_tick_frequency_hz)(void *context, uint32_t *frequency_hz);
} led_timer_t;

/**
 * @brief   LED control interface structure.
 * @details This structure defines the abstract interface to control an LED.
 *          The user must populate these function pointers with platform-specific
 *          implementations (e.g., GPIO write) before calling @ref led_blink_timer_init.
 *
 *          - @ref set_on: Turns the LED on.
 *          - @ref set_off: Turns the LED off.
 */
typedef struct {
    /**
     * @brief   Turns the LED on.
     * @param   context: User-defined context pointer (e.g., GPIO port and pin).
     * @return  0 on success, negative error code on failure.
     */
    int (*set_on)(void *context);

    /**
     * @brief   Turns the LED off.
     * @param   context: User-defined context pointer.
     * @return  0 on success, negative error code on failure.
     */
    int (*set_off)(void *context);
} led_io_t;

/**
 * @brief   LED blink timer instance structure.
 * @details This structure holds the state and configuration for a single LED blink timer.
 *          It is intended to be instantiated by the user (e.g., as a global or local variable).
 *          The user should not modify its fields directly after initialization.
 */
typedef struct {
    const led_timer_t *timer;       /**< Pointer to the timer interface. */
    const led_io_t    *led;         /**< Pointer to the LED control interface. */
    void              *timer_ctx;   /**< User context for the timer interface. */
    void              *led_ctx;     /**< User context for the LED control interface. */
    uint32_t           period_ticks; /**< Number of timer ticks for a half-period (on or off). */
    uint32_t           last_tick;   /**< Last recorded tick count for timing calculations. */
    bool               led_state;   /**< Current state of the LED (true = on, false = off). */
    bool               initialized; /**< Flag indicating if the instance has been initialized. */
} led_blink_timer_t;

/**
 * @brief   Initializes a LED blink timer instance.
 * @param   instance: Pointer to the LED blink timer instance to initialize.
 * @param   timer:    Pointer to the timer interface structure.
 * @param   led:      Pointer to the LED control interface structure.
 * @param   timer_ctx: User context pointer for the timer interface (can be NULL).
 * @param   led_ctx:   User context pointer for the LED control interface (can be NULL).
 * @param   frequency_hz: Desired blink frequency in Hz (e.g., 1 for 1Hz).
 * @return  @ref LED_BLINK_TIMER_OK on success, or a negative error code.
 * @retval  LED_BLINK_TIMER_ERR_NULL_PTR     If instance, timer, or led is NULL.
 * @retval  LED_BLINK_TIMER_ERR_INVALID_FREQ If frequency_hz is out of range.
 * @retval  LED_BLINK_TIMER_ERR_TIMER_FAIL   If the timer interface fails to start or get frequency.
 */
int led_blink_timer_init(led_blink_timer_t *instance,
                         const led_timer_t *timer,
                         const led_io_t *led,
                         void *timer_ctx,
                         void *led_ctx,
                         uint32_t frequency_hz);

/**
 * @brief   Performs the periodic update for the LED blink timer.
 * @details This function should be called repeatedly from the main loop or a
 *          low-priority task. It checks the current timer tick count against
 *          the last recorded tick and toggles the LED state when the half-period
 *          has elapsed.
 * @param   instance: Pointer to the initialized LED blink timer instance.
 * @return  @ref LED_BLINK_TIMER_OK on success, or a negative error code.
 * @retval  LED_BLINK_TIMER_ERR_NULL_PTR   If instance is NULL.
 * @retval  LED_BLINK_TIMER_ERR_TIMER_FAIL If the timer interface fails to get ticks.
 */
int led_blink_timer_update(led_blink_timer_t *instance);

/**
 * @brief   Stops the LED blink timer and turns the LED off.
 * @param   instance: Pointer to the initialized LED blink timer instance.
 * @return  @ref LED_BLINK_TIMER_OK on success, or a negative error code.
 * @retval  LED_BLINK_TIMER_ERR_NULL_PTR   If instance is NULL.
 * @retval  LED_BLINK_TIMER_ERR_TIMER_FAIL If the timer interface fails to stop.
 */
int led_blink_timer_stop(led_blink_timer_t *instance);

#ifdef __cplusplus
}
#endif

#endif /* LED_BLINK_TIMER_H */
