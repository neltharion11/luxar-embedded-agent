/**
 * @file    rgb_led_red.h
 * @brief   MCU-agnostic driver for the red channel of an RGB LED via GPIO.
 *
 * This driver provides an abstraction to control the red channel of an RGB LED.
 * It uses a HAL function-pointer table for GPIO operations, making it
 * independent of any specific MCU or HAL implementation.
 *
 * @note    This driver is designed for bare-metal GPIO control. It does not
 *          require any timer, UART, SPI, or I2C peripherals.
 */

#ifndef RGB_LED_RED_H
#define RGB_LED_RED_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>

/**
 * @brief   Maximum number of retry attempts for an operation (not used in this
 *          simple driver, but defined for consistency).
 */
#define RGB_LED_RED_MAX_RETRY      (3U)

/**
 * @brief   Error codes for the RGB LED red driver.
 */
#define RGB_LED_RED_OK             (0)      /**< Operation successful. */
#define RGB_LED_RED_ERR_NULL_PTR   (-1)     /**< NULL pointer passed to function. */
#define RGB_LED_RED_ERR_INIT       (-2)     /**< Driver not initialized. */
#define RGB_LED_RED_ERR_PARAM      (-3)     /**< Invalid parameter. */

/**
 * @brief   GPIO pin state.
 */
typedef enum {
    RGB_LED_RED_PIN_RESET = 0u, /**< GPIO pin output low. */
    RGB_LED_RED_PIN_SET   = 1u  /**< GPIO pin output high. */
} rgb_led_red_pin_state_t;

/**
 * @brief   HAL abstraction structure for GPIO operations.
 *
 * This structure contains function pointers for the GPIO operations required
 * by this driver. The user must provide an initialized instance of this
 * structure before using the driver.
 */
typedef struct {
    /**
     * @brief   Write the output state of a GPIO pin.
     * @param   port    Base address of the GPIO port.
     * @param   pin     Pin number (0-15).
     * @param   state   Desired pin state (SET or RESET).
     * @retval  0       Operation successful.
     * @retval  -1      Operation failed.
     */
    int32_t (*gpio_write_pin)(void *port, uint16_t pin, uint8_t state);

    /**
     * @brief   Configure a GPIO pin as output push-pull.
     * @param   port    Base address of the GPIO port.
     * @param   pin     Pin number (0-15).
     * @retval  0       Operation successful.
     * @retval  -1      Operation failed.
     */
    int32_t (*gpio_set_output_pp)(void *port, uint16_t pin);
} rgb_led_red_hal_t;

/**
 * @brief   Configuration structure for the RGB LED red channel.
 */
typedef struct {
    void     *gpio_port; /**< Base address of the GPIO port (e.g., GPIOB). */
    uint16_t  gpio_pin;  /**< Pin number (e.g., 12 for PB12). */
} rgb_led_red_cfg_t;

/**
 * @brief   Instance structure for the RGB LED red channel driver.
 *
 * This structure holds the state and configuration for a single instance of
 * the driver. It is intended to be statically allocated by the user.
 */
typedef struct {
    const rgb_led_red_hal_t *hal; /**< Pointer to the HAL abstraction table. */
    rgb_led_red_cfg_t        cfg; /**< Driver configuration. */
    uint8_t                  initialized; /**< Driver initialization flag. */
} rgb_led_red_t;

/**
 * @brief   Initialize the RGB LED red channel driver instance.
 *
 * This function configures the GPIO pin as an output push-pull and stores the
 * HAL function pointers and configuration for later use.
 *
 * @param[in]   p_led   Pointer to the driver instance structure.
 * @param[in]   p_hal   Pointer to the HAL abstraction structure.
 * @param[in]   p_cfg   Pointer to the configuration structure.
 *
 * @retval  0                    Operation successful.
 * @retval  RGB_LED_RED_ERR_NULL_PTR   A required pointer argument is NULL.
 */
int32_t rgb_led_red_init(rgb_led_red_t *p_led,
                         const rgb_led_red_hal_t *p_hal,
                         const rgb_led_red_cfg_t *p_cfg);

/**
 * @brief   Turn on the red channel of the RGB LED.
 *
 * This function sets the configured GPIO pin to a high state.
 *
 * @param[in]   p_led   Pointer to the initialized driver instance.
 *
 * @retval  0                    Operation successful.
 * @retval  RGB_LED_RED_ERR_NULL_PTR   The instance pointer is NULL.
 * @retval  RGB_LED_RED_ERR_INIT       The driver instance is not initialized.
 */
int32_t rgb_led_red_on(rgb_led_red_t *p_led);

/**
 * @brief   Turn off the red channel of the RGB LED.
 *
 * This function sets the configured GPIO pin to a low state.
 *
 * @param[in]   p_led   Pointer to the initialized driver instance.
 *
 * @retval  0                    Operation successful.
 * @retval  RGB_LED_RED_ERR_NULL_PTR   The instance pointer is NULL.
 * @retval  RGB_LED_RED_ERR_INIT       The driver instance is not initialized.
 */
int32_t rgb_led_red_off(rgb_led_red_t *p_led);

#ifdef __cplusplus
}
#endif

#endif /* RGB_LED_RED_H */
