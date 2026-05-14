/**
 * @file rgb_led.h
 * @brief MCU-independent RGB LED driver using PWM.
 *
 * This driver controls an RGB LED via three PWM channels. The PWM hardware
 * abstraction is injected through a structure of function pointers, allowing
 * the same driver to be ported to any MCU/RTOS without modification.
 *
 * The driver also implements HSV-to-RGB conversion for easy rainbow effects.
 */

#ifndef RGB_LED_H
#define RGB_LED_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---------------------------------------------------------------------------
 * Error codes (negative values)
 * -------------------------------------------------------------------------*/
#define RGB_LED_OK                   0
#define RGB_LED_ERR_NULL_PTR        -1
#define RGB_LED_ERR_PWM_FAIL        -2
#define RGB_LED_ERR_INVALID_PARAM   -3

/* ---------------------------------------------------------------------------
 * PWM interface – must be implemented by the platform adapter
 * -------------------------------------------------------------------------*/
typedef struct {
    /**
     * @brief Initialize a PWM channel.
     * @param channel   Channel identifier (0,1,2...).
     * @param period    PWM period in timer ticks (e.g., 255 for 8-bit).
     * @return 0 on success, negative on error.
     */
    int (*init)(uint32_t channel, uint32_t period);

    /**
     * @brief Set the PWM duty cycle for a channel.
     * @param channel   Channel identifier.
     * @param duty      Duty value (0 .. period).
     * @return 0 on success, negative on error.
     */
    int (*set_duty)(uint32_t channel, uint32_t duty);

    /**
     * @brief De-initialize a PWM channel.
     * @param channel   Channel identifier.
     * @return 0 on success, negative on error.
     */
    int (*deinit)(uint32_t channel);
} rgb_led_pwm_t;

/* ---------------------------------------------------------------------------
 * RGB LED instance
 * -------------------------------------------------------------------------*/
typedef struct {
    const rgb_led_pwm_t *pwm;       /**< Injected PWM interface (not owned) */
    uint32_t chan_r;                /**< Red PWM channel index */
    uint32_t chan_g;                /**< Green PWM channel index */
    uint32_t chan_b;                /**< Blue PWM channel index */
    uint32_t period;                /**< PWM period (max duty) */

    /* current output values (0..period) */
    uint32_t curr_r;
    uint32_t curr_g;
    uint32_t curr_b;

    /* state for rainbow cycling */
    uint16_t rainbow_hue;           /**< Current hue (0..360) */
} rgb_led_t;

/* ---------------------------------------------------------------------------
 * Public API
 * -------------------------------------------------------------------------*/

/**
 * @brief Initialize an RGB LED instance and its PWM channels.
 *
 * @param led       Pointer to an uninitialized #rgb_led_t.
 * @param pwm       Pointer to a const #rgb_led_pwm_t implementation.
 * @param chan_r    PWM channel for Red.
 * @param chan_g    PWM channel for Green.
 * @param chan_b    PWM channel for Blue.
 * @param period    PWM period (e.g., 255 for 8-bit resolution).
 * @return #RGB_LED_OK on success, or negative error code.
 */
int rgb_led_init(rgb_led_t *led,
                 const rgb_led_pwm_t *pwm,
                 uint32_t chan_r,
                 uint32_t chan_g,
                 uint32_t chan_b,
                 uint32_t period);

/**
 * @brief Set the RGB color (each value in range 0..255).
 *
 * Values are scaled to the configured PWM period automatically.
 *
 * @param led   Pointer to an initialized #rgb_led_t.
 * @param r     Red intensity (0..255).
 * @param g     Green intensity (0..255).
 * @param b     Blue intensity (0..255).
 * @return #RGB_LED_OK on success, negative on error.
 */
int rgb_led_set_rgb(rgb_led_t *led, uint8_t r, uint8_t g, uint8_t b);

/**
 * @brief Set the color using HSV model.
 *
 * @param led   Pointer to an initialized #rgb_led_t.
 * @param h     Hue (0..360 degrees).
 * @param s     Saturation (0..255).
 * @param v     Value (0..255).
 * @return #RGB_LED_OK on success, negative on error.
 */
int rgb_led_set_hsv(rgb_led_t *led, uint16_t h, uint8_t s, uint8_t v);

/**
 * @brief Advance the rainbow hue and update the LED.
 *
 * Call this periodically (e.g., every 30 ms) for a smooth cycling effect.
 * Each call increments the hue by 1 (wrap at 360).
 *
 * @param led   Pointer to an initialized #rgb_led_t.
 * @return #RGB_LED_OK on success, negative on error.
 */
int rgb_led_rainbow_step(rgb_led_t *led);

/**
 * @brief Turn off all three LEDs.
 *
 * Sets all duty cycles to 0.
 *
 * @param led   Pointer to an initialized #rgb_led_t.
 * @return #RGB_LED_OK on success, negative on error.
 */
int rgb_led_off(rgb_led_t *led);

/**
 * @brief De-initialize the RGB LED (stop PWM channels).
 *
 * @param led   Pointer to an initialized #rgb_led_t.
 * @return #RGB_LED_OK on success, negative on error.
 */
int rgb_led_deinit(rgb_led_t *led);

#ifdef __cplusplus
}
#endif

#endif /* RGB_LED_H */
