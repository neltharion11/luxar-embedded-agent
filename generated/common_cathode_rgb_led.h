#ifndef RGB_LED_DRV_H
#define RGB_LED_DRV_H

/**
 * @file    rgb_led_drv.h
 * @brief   MCU-independent driver for a common-cathode RGB LED.
 *          Three GPIO channels (R, G, B) are controlled via injected
 *          write function pointers.  Software PWM is used for dimming
 *          and colour mixing.
 */

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ── Error codes ──────────────────────────────────────────────────────── */
#define RGB_LED_OK         0   /**< Operation succeeded */
#define RGB_LED_ERR_PARAM -1   /**< Invalid parameter (NULL pointer, bad period, etc.) */

/* ── Channel identifiers ──────────────────────────────────────────────── */
typedef enum {
    RGB_LED_CHANNEL_RED   = 0, /**< Red channel (PB0) */
    RGB_LED_CHANNEL_GREEN = 1, /**< Green channel (PA7) */
    RGB_LED_CHANNEL_BLUE  = 2  /**< Blue channel (PA6) */
} rgb_led_channel_t;

/* ── GPIO abstraction ─────────────────────────────────────────────────── */
/**
 * @brief   Callback to set a GPIO output pin state.
 * @param   context   Opaque pointer passed at initialisation (may be NULL).
 * @param   channel   Which RGB channel to control.
 * @param   state     0 = LOW, any other value = HIGH.
 */
typedef void (*rgb_led_gpio_write_t)(void *context, rgb_led_channel_t channel, uint8_t state);

/* ── Effect modes ─────────────────────────────────────────────────────── */
typedef enum {
    RGB_LED_EFFECT_NONE,            /**< No effect – colour held at set value */
    RGB_LED_EFFECT_RAINBOW,         /**< Colour cycles through the full spectrum */
    RGB_LED_EFFECT_BREATHING,       /**< Single colour fades in/out (last set colour) */
    RGB_LED_EFFECT_COLOR_CHASE,     /**< Sequentially walk through R→G→B→mix */
    RGB_LED_EFFECT_RANDOM_STROBE   /**< Random colour flashes at random intervals */
} rgb_led_effect_t;

/* ── Driver instance ──────────────────────────────────────────────────── */
typedef struct {
    /* GPIO abstraction */
    rgb_led_gpio_write_t  write_pin;      /**< Injected pin-write function (non-NULL) */
    void                 *gpio_context;   /**< User-defined context for write_pin */

    /* Current PWM target brightness (0 … 255) */
    uint8_t  red;
    uint8_t  green;
    uint8_t  blue;

    /* Software PWM state */
    uint16_t pwm_counter;                 /**< 0 … pwm_period-1, incremented per update */
    uint16_t pwm_period;                  /**< Number of steps in one PWM cycle (≥ 2) */

    /* Current effect & timing */
    rgb_led_effect_t effect;
    uint16_t          effect_step;        /**< Effect state progression counter */
    uint16_t          effect_delay_ms;    /**< Milliseconds between effect steps */
    uint32_t          last_tick_ms;       /**< Timestamp of last effect advance */

    /* Internal scratch for colour chase */
    uint8_t chase_colors[3];              /**< Current chase triplet (R,G,B) */
    uint8_t chase_index;                  /**< Index in chase colour table */
} rgb_led_t;

/* ── Public API ───────────────────────────────────────────────────────── */

/**
 * @brief   Initialise an RGB LED driver instance.
 * @param   led         Pointer to driver instance (must not be NULL).
 * @param   write_fn    GPIO write function (must not be NULL).
 * @param   context     Optional opaque pointer passed to write_fn.
 * @param   pwm_period  Number of update ticks per PWM cycle (>= 2).
 * @return  RGB_LED_OK on success, RGB_LED_ERR_PARAM on invalid input.
 */
int rgb_led_init(rgb_led_t *led,
                 rgb_led_gpio_write_t write_fn,
                 void *context,
                 uint16_t pwm_period);

/**
 * @brief   Set the target colour (disables any running effect).
 * @param   led    Pointer to initialised driver instance.
 * @param   red    Red brightness 0 … 255.
 * @param   green  Green brightness 0 … 255.
 * @param   blue   Blue brightness 0 … 255.
 * @return  RGB_LED_OK on success, RGB_LED_ERR_PARAM if led is NULL.
 */
int rgb_led_set_color(rgb_led_t *led,
                      uint8_t red, uint8_t green, uint8_t blue);

/**
 * @brief   Periodic update – must be called at the PWM tick rate
 *          (e.g. every 1 ms from a timer or RTOS thread).
 * @param   led              Pointer to driver instance.
 * @param   current_tick_ms  Free-running millisecond counter (e.g. HAL_GetTick).
 * @return  RGB_LED_OK on success, negative error code on failure.
 */
int rgb_led_pwm_update(rgb_led_t *led, uint32_t current_tick_ms);

/* ── Effect control ───────────────────────────────────────────────────── */

/**
 * @brief   Start a rainbow cycle effect.
 * @param   led       Pointer to driver instance.
 * @param   step_ms   Milliseconds between hue steps (≥1).
 * @return  RGB_LED_OK or error code.
 */
int rgb_led_effect_rainbow(rgb_led_t *led, uint16_t step_ms);

/**
 * @brief   Start a breathing effect (fade in/out of the last set colour).
 * @param   led       Pointer to driver instance.
 * @param   step_ms   Milliseconds between brightness steps (≥1).
 * @return  RGB_LED_OK or error code.
 */
int rgb_led_effect_breathing(rgb_led_t *led, uint16_t step_ms);

/**
 * @brief   Start a colour chase effect (cycles through predefined colour slots).
 * @param   led       Pointer to driver instance.
 * @param   step_ms   Milliseconds between colour switches (≥1).
 * @return  RGB_LED_OK or error code.
 */
int rgb_led_effect_color_chase(rgb_led_t *led, uint16_t step_ms);

/**
 * @brief   Start a random strobe effect (random colour, random pause).
 * @param   led       Pointer to driver instance.
 * @param   step_ms   Base ms interval between strobes (actual = step_ms * random factor).
 * @return  RGB_LED_OK or error code.
 */
int rgb_led_effect_random_strobe(rgb_led_t *led, uint16_t step_ms);

/**
 * @brief   Stop the currently running effect (colour stays at last value).
 * @param   led   Pointer to driver instance.
 * @return  RGB_LED_OK or error code.
 */
int rgb_led_effect_stop(rgb_led_t *led);

#ifdef __cplusplus
}
#endif

#endif /* RGB_LED_DRV_H */
