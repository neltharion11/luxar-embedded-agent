/**
 * @file    led_driver.h
 * @brief   MCU-agnostic driver for controlling an LED via GPIO.
 *
 * @details This driver provides an abstraction layer for controlling a single
 *          LED connected to a GPIO pin. It uses a HAL function-pointer table
 *          to remain independent of the underlying MCU and HAL implementation.
 *          The driver is designed for bare-metal register access.
 *
 *          Expected behavior: On initialization, the LED is turned off.
 *          The `led_driver_toggle()` function changes the LED state.
 *
 * @note    This driver does not use printf, malloc, or free.
 *          All pointer parameters are checked for NULL.
 */

#ifndef LED_DRIVER_H
#define LED_DRIVER_H

#include <stdint.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief   Maximum number of supported LED instances.
 *
 * @details This value can be adjusted based on project requirements.
 *          It defines the size of the internal instance tracking array.
 */
#define LED_DRIVER_MAX_INSTANCES    1U

/**
 * @brief   Error codes for the LED driver.
 */
typedef enum {
    LED_DRIVER_OK               =  0, /**< Operation completed successfully. */
    LED_DRIVER_ERR_NULL_PTR     = -1, /**< A NULL pointer was passed as an argument. */
    LED_DRIVER_ERR_INIT_FAILED  = -2, /**< Initialization of the HAL interface failed. */
    LED_DRIVER_ERR_INVALID_PIN  = -3, /**< The specified GPIO pin is invalid. */
    LED_DRIVER_ERR_INSTANCE     = -4  /**< Invalid instance handle or maximum instances reached. */
} led_driver_error_t;

/**
 * @brief   HAL function pointer table for GPIO operations.
 *
 * @details This structure defines the interface between the LED driver and
 *          the platform-specific HAL. The user must populate this structure
 *          with pointers to their platform's GPIO functions before calling
 *          `led_driver_init()`.
 *
 *          All functions in this table must follow the signature:
 *          - `init`: Initialize the GPIO pin for output.
 *          - `write`: Set the output state of the GPIO pin (true = high, false = low).
 *          - `read`: Read the current input state of the GPIO pin (true = high, false = low).
 */
typedef struct {
    /**
     * @brief   Initialize a GPIO pin as a push-pull output.
     *
     * @param   port    Base address of the GPIO port (e.g., (void*)GPIOC_BASE).
     * @param   pin     Pin number (0-15).
     * @param   speed   Output speed (e.g., 0=2MHz, 1=10MHz, 2=50MHz).
     * @return  int     0 on success, negative error code on failure.
     */
    int (*init)(void *port, uint16_t pin, uint8_t speed);

    /**
     * @brief   Write a digital value to a GPIO pin.
     *
     * @param   port    Base address of the GPIO port.
     * @param   pin     Pin number (0-15).
     * @param   state   true for high level, false for low level.
     * @return  int     0 on success, negative error code on failure.
     */
    int (*write)(void *port, uint16_t pin, bool state);

    /**
     * @brief   Read the digital value from a GPIO pin.
     *
     * @param   port    Base address of the GPIO port.
     * @param   pin     Pin number (0-15).
     * @return  int     Positive value (1) for high, 0 for low, negative error code on failure.
     */
    int (*read)(void *port, uint16_t pin);
} led_driver_hal_t;

/**
 * @brief   LED driver instance handle.
 *
 * @details This is an opaque handle. The user obtains it from `led_driver_init()`
 *          and uses it for all subsequent operations.
 */
typedef struct led_driver_instance led_driver_instance_t;

/**
 * @brief   Initialize an LED driver instance.
 *
 * @param   hal     Pointer to a populated HAL function table. Must not be NULL.
 * @param   port    Base address of the GPIO port. Must not be NULL.
 * @param   pin     Pin number (0-15).
 * @param   speed   Output speed (e.g., 0=2MHz, 1=10MHz, 2=50MHz).
 *
 * @return  Pointer to the initialized LED driver instance on success.
 *          NULL on failure (e.g., NULL pointer, invalid pin, max instances reached).
 */
led_driver_instance_t* led_driver_init(const led_driver_hal_t *hal, void *port, uint16_t pin, uint8_t speed);

/**
 * @brief   Turn the LED on.
 *
 * @param   instance    Pointer to the LED driver instance. Must not be NULL.
 *
 * @return  int         0 on success, negative error code on failure.
 */
int led_driver_on(led_driver_instance_t *instance);

/**
 * @brief   Turn the LED off.
 *
 * @param   instance    Pointer to the LED driver instance. Must not be NULL.
 *
 * @return  int         0 on success, negative error code on failure.
 */
int led_driver_off(led_driver_instance_t *instance);

/**
 * @brief   Toggle the LED state.
 *
 * @param   instance    Pointer to the LED driver instance. Must not be NULL.
 *
 * @return  int         0 on success, negative error code on failure.
 */
int led_driver_toggle(led_driver_instance_t *instance);

/**
 * @brief   Get the current state of the LED.
 *
 * @param   instance    Pointer to the LED driver instance. Must not be NULL.
 * @param   state       Pointer to a bool to receive the state. Must not be NULL.
 *                      true = LED is on, false = LED is off.
 *
 * @return  int         0 on success, negative error code on failure.
 */
int led_driver_get_state(led_driver_instance_t *instance, bool *state);

/**
 * @brief   De-initialize the LED driver instance and release resources.
 *
 * @param   instance    Pointer to the LED driver instance. Must not be NULL.
 *                      The pointer will be set to NULL after de-initialization.
 *
 * @return  int         0 on success, negative error code on failure.
 */
int led_driver_deinit(led_driver_instance_t **instance);

#ifdef __cplusplus
}
#endif

#endif /* LED_DRIVER_H */
