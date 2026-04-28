/**
 * @file    rgb_led.h
 * @brief   MCU-independent RGB LED driver (GPIO-based)
 * @details Provides an interface to control an RGB LED via GPIO pins.
 *          The driver uses function pointer injection to remain platform-agnostic.
 *          Supports individual channel control (RED, GREEN, BLUE) and combined color setting.
 *
 * @note    This driver is designed for bare-metal register programming and does not use HAL.
 *          All HAL operations are abstracted through the rgb_led_hal_t structure.
 */

#ifndef RGB_LED_H
#define RGB_LED_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stdbool.h>

/* -------------------------------------------------------------------------- */
/*                              Error Codes                                   */
/* -------------------------------------------------------------------------- */
#define RGB_LED_OK              0
#define RGB_LED_ERR_NULL_PTR   -1
#define RGB_LED_ERR_INVALID_PIN -2
#define RGB_LED_ERR_HAL_FAIL    -3

/* -------------------------------------------------------------------------- */
/*                              Channel Definitions                           */
/* -------------------------------------------------------------------------- */
/** @brief RGB LED color channels */
typedef enum {
    RGB_LED_CHANNEL_RED   = 0,    /**< Red channel */
    RGB_LED_CHANNEL_GREEN = 1,    /**< Green channel */
    RGB_LED_CHANNEL_BLUE  = 2,    /**< Blue channel */
    RGB_LED_CHANNEL_COUNT = 3     /**< Number of channels */
} rgb_led_channel_t;

/** @brief LED state */
typedef enum {
    RGB_LED_STATE_OFF = 0,        /**< LED off (pin low) */
    RGB_LED_STATE_ON  = 1         /**< LED on (pin high) */
} rgb_led_state_t;

/* -------------------------------------------------------------------------- */
/*                              HAL Abstraction                               */
/* -------------------------------------------------------------------------- */
/**
 * @brief HAL function pointers for GPIO operations.
 * @details The user must provide implementations for these functions.
 *          All functions return 0 on success, negative on error.
 */
typedef struct {
    /**
     * @brief Set a GPIO pin high.
     * @param[in] port     GPIO port base address (e.g., GPIOB_BASE from CMSIS headers)
     * @param[in] pin      Pin number (0-15)
     * @return 0 on success, negative error code on failure
     */
    int (*gpio_set_pin)(void* port, uint8_t pin);

    /**
     * @brief Set a GPIO pin low.
     * @param[in] port     GPIO port base address
     * @param[in] pin      Pin number (0-15)
     * @return 0 on success, negative error code on failure
     */
    int (*gpio_reset_pin)(void* port, uint8_t pin);

    /**
     * @brief Configure a GPIO pin as push-pull output.
     * @param[in] port     GPIO port base address
     * @param[in] pin      Pin number (0-15)
     * @param[in] speed    Output speed (0=low, 1=medium, 2=high, 3=very high)
     * @param[in] pull     Pull configuration (0=no pull, 1=pull-up, 2=pull-down)
     * @return 0 on success, negative error code on failure
     */
    int (*gpio_configure_output)(void* port, uint8_t pin, uint8_t speed, uint8_t pull);

    /**
     * @brief Enable clock for a GPIO port.
     * @param[in] port     GPIO port base address
     * @return 0 on success, negative error code on failure
     */
    int (*gpio_enable_clock)(void* port);
} rgb_led_hal_t;

/* -------------------------------------------------------------------------- */
/*                              Driver Configuration                          */
/* -------------------------------------------------------------------------- */
/**
 * @brief RGB LED channel configuration.
 */
typedef struct {
    void*       port;       /**< GPIO port base address for this channel */
    uint8_t     pin;        /**< GPIO pin number for this channel */
    bool        active_high;/**< true = pin high turns LED on, false = pin low turns LED on */
} rgb_led_channel_config_t;

/**
 * @brief RGB LED driver configuration.
 */
typedef struct {
    rgb_led_channel_config_t channels[RGB_LED_CHANNEL_COUNT]; /**< Configuration for each channel */
    const rgb_led_hal_t*     hal;                             /**< HAL function pointers */
} rgb_led_config_t;

/* -------------------------------------------------------------------------- */
/*                              Driver Instance                               */
/* -------------------------------------------------------------------------- */
/**
 * @brief RGB LED driver instance (opaque structure).
 */
typedef struct rgb_led_instance rgb_led_instance_t;

/* -------------------------------------------------------------------------- */
/*                              Public API                                    */
/* -------------------------------------------------------------------------- */

/**
 * @brief Initialize an RGB LED driver instance.
 * @param[out] instance   Pointer to driver instance handle (allocated by caller)
 * @param[in]  config     Driver configuration
 * @return RGB_LED_OK on success, negative error code on failure
 * @retval RGB_LED_ERR_NULL_PTR if instance or config is NULL
 * @retval RGB_LED_ERR_NULL_PTR if config->hal or any hal function pointer is NULL
 * @retval RGB_LED_ERR_INVALID_PIN if any pin number > 15
 */
int rgb_led_init(rgb_led_instance_t* instance, const rgb_led_config_t* config);

/**
 * @brief Set the state of a specific RGB LED channel.
 * @param[in] instance   Driver instance
 * @param[in] channel    Channel to control (RGB_LED_CHANNEL_RED, _GREEN, _BLUE)
 * @param[in] state      Desired state (RGB_LED_STATE_ON or RGB_LED_STATE_OFF)
 * @return RGB_LED_OK on success, negative error code on failure
 * @retval RGB_LED_ERR_NULL_PTR if instance is NULL
 * @retval RGB_LED_ERR_INVALID_PIN if channel is out of range
 * @retval RGB_LED_ERR_HAL_FAIL if underlying HAL operation fails
 */
int rgb_led_set_channel(rgb_led_instance_t* instance, rgb_led_channel_t channel, rgb_led_state_t state);

/**
 * @brief Set all RGB LED channels to a specific state.
 * @param[in] instance   Driver instance
 * @param[in] state      Desired state for all channels
 * @return RGB_LED_OK on success, negative error code on failure
 */
int rgb_led_set_all(rgb_led_instance_t* instance, rgb_led_state_t state);

/**
 * @brief Set a combined color by specifying states for all three channels.
 * @param[in] instance   Driver instance
 * @param[in] red        State for red channel
 * @param[in] green      State for green channel
 * @param[in] blue       State for blue channel
 * @return RGB_LED_OK on success, negative error code on failure
 */
int rgb_led_set_color(rgb_led_instance_t* instance, rgb_led_state_t red, rgb_led_state_t green, rgb_led_state_t blue);

/**
 * @brief Deinitialize the RGB LED driver and release resources.
 * @param[in] instance   Driver instance
 * @return RGB_LED_OK on success, negative error code on failure
 */
int rgb_led_deinit(rgb_led_instance_t* instance);

#ifdef __cplusplus
}
#endif

#endif /* RGB_LED_H */
