/**
 * @file    rgb_led.c
 * @brief   Implementation of MCU-independent RGB LED driver
 * @details Implements the RGB LED driver using function pointer injection
 *          for GPIO operations. All HAL dependencies are abstracted.
 */

#include "rgb_led.h"
#include <stddef.h> /* for NULL */

/* -------------------------------------------------------------------------- */
/*                              Driver Instance Structure                     */
/* -------------------------------------------------------------------------- */
struct rgb_led_instance {
    rgb_led_channel_config_t channels[RGB_LED_CHANNEL_COUNT]; /**< Channel configurations */
    const rgb_led_hal_t*     hal;                             /**< HAL function pointers */
    bool                     initialized;                     /**< Initialization flag */
};

/* -------------------------------------------------------------------------- */
/*                              Internal Helpers                              */
/* -------------------------------------------------------------------------- */

/**
 * @brief Validate HAL function pointers.
 * @param[in] hal   HAL structure to validate
 * @return true if all pointers are non-NULL, false otherwise
 */
static bool is_hal_valid(const rgb_led_hal_t* hal)
{
    if (hal == NULL)
    {
        return false;
    }
    if (hal->gpio_set_pin == NULL)
    {
        return false;
    }
    if (hal->gpio_reset_pin == NULL)
    {
        return false;
    }
    if (hal->gpio_configure_output == NULL)
    {
        return false;
    }
    if (hal->gpio_enable_clock == NULL)
    {
        return false;
    }
    return true;
}

/**
 * @brief Validate channel configuration.
 * @param[in] config   Channel configuration to validate
 * @return true if valid, false otherwise
 */
static bool is_channel_config_valid(const rgb_led_channel_config_t* config)
{
    if (config == NULL)
    {
        return false;
    }
    if (config->port == NULL)
    {
        return false;
    }
    if (config->pin > 15U)
    {
        return false;
    }
    return true;
}

/**
 * @brief Apply state to a single channel using HAL abstraction.
 * @param[in] instance   Driver instance
 * @param[in] channel    Channel index
 * @param[in] state      Desired state
 * @return RGB_LED_OK on success, negative error code on failure
 */
static int apply_channel_state(rgb_led_instance_t* instance, uint8_t channel, rgb_led_state_t state)
{
    int ret;
    const rgb_led_channel_config_t* ch_cfg = &instance->channels[channel];
    const rgb_led_hal_t* hal = instance->hal;

    if (state == RGB_LED_STATE_ON)
    {
        if (ch_cfg->active_high)
        {
            ret = hal->gpio_set_pin(ch_cfg->port, ch_cfg->pin);
        }
        else
        {
            ret = hal->gpio_reset_pin(ch_cfg->port, ch_cfg->pin);
        }
    }
    else /* RGB_LED_STATE_OFF */
    {
        if (ch_cfg->active_high)
        {
            ret = hal->gpio_reset_pin(ch_cfg->port, ch_cfg->pin);
        }
        else
        {
            ret = hal->gpio_set_pin(ch_cfg->port, ch_cfg->pin);
        }
    }

    if (ret != 0)
    {
        return RGB_LED_ERR_HAL_FAIL;
    }
    return RGB_LED_OK;
}

/* -------------------------------------------------------------------------- */
/*                              Public API Implementation                     */
/* -------------------------------------------------------------------------- */

int rgb_led_init(rgb_led_instance_t* instance, const rgb_led_config_t* config)
{
    int ret;
    uint8_t i;

    /* NULL pointer checks */
    if (instance == NULL)
    {
        return RGB_LED_ERR_NULL_PTR;
    }
    if (config == NULL)
    {
        return RGB_LED_ERR_NULL_PTR;
    }

    /* Validate HAL */
    if (!is_hal_valid(config->hal))
    {
        return RGB_LED_ERR_NULL_PTR;
    }

    /* Validate channel configurations */
    for (i = 0; i < RGB_LED_CHANNEL_COUNT; i++)
    {
        if (!is_channel_config_valid(&config->channels[i]))
        {
            return RGB_LED_ERR_INVALID_PIN;
        }
    }

    /* Store configuration */
    instance->hal = config->hal;
    for (i = 0; i < RGB_LED_CHANNEL_COUNT; i++)
    {
        instance->channels[i] = config->channels[i];
    }

    /* Initialize each channel: enable clock, configure as output, default off */
    for (i = 0; i < RGB_LED_CHANNEL_COUNT; i++)
    {
        const rgb_led_channel_config_t* ch_cfg = &instance->channels[i];

        /* Enable clock */
        ret = instance->hal->gpio_enable_clock(ch_cfg->port);
        if (ret != 0)
        {
            return RGB_LED_ERR_HAL_FAIL;
        }

        /* Configure as push-pull output, low speed, no pull */
        ret = instance->hal->gpio_configure_output(ch_cfg->port, ch_cfg->pin, 0U, 0U);
        if (ret != 0)
        {
            return RGB_LED_ERR_HAL_FAIL;
        }

        /* Set initial state to OFF */
        ret = apply_channel_state(instance, i, RGB_LED_STATE_OFF);
        if (ret != 0)
        {
            return ret;
        }
    }

    instance->initialized = true;
    return RGB_LED_OK;
}

int rgb_led_set_channel(rgb_led_instance_t* instance, rgb_led_channel_t channel, rgb_led_state_t state)
{
    /* NULL pointer check */
    if (instance == NULL)
    {
        return RGB_LED_ERR_NULL_PTR;
    }

    /* Check if initialized */
    if (!instance->initialized)
    {
        return RGB_LED_ERR_HAL_FAIL;
    }

    /* Validate channel */
    if (channel >= RGB_LED_CHANNEL_COUNT)
    {
        return RGB_LED_ERR_INVALID_PIN;
    }

    return apply_channel_state(instance, (uint8_t)channel, state);
}

int rgb_led_set_all(rgb_led_instance_t* instance, rgb_led_state_t state)
{
    int ret;
    uint8_t i;

    /* NULL pointer check */
    if (instance == NULL)
    {
        return RGB_LED_ERR_NULL_PTR;
    }

    /* Check if initialized */
    if (!instance->initialized)
    {
        return RGB_LED_ERR_HAL_FAIL;
    }

    for (i = 0; i < RGB_LED_CHANNEL_COUNT; i++)
    {
        ret = apply_channel_state(instance, i, state);
        if (ret != 0)
        {
            return ret;
        }
    }

    return RGB_LED_OK;
}

int rgb_led_set_color(rgb_led_instance_t* instance, rgb_led_state_t red, rgb_led_state_t green, rgb_led_state_t blue)
{
    int ret;

    /* NULL pointer check */
    if (instance == NULL)
    {
        return RGB_LED_ERR_NULL_PTR;
    }

    /* Check if initialized */
    if (!instance->initialized)
    {
        return RGB_LED_ERR_HAL_FAIL;
    }

    ret = apply_channel_state(instance, RGB_LED_CHANNEL_RED, red);
    if (ret != 0)
    {
        return ret;
    }

    ret = apply_channel_state(instance, RGB_LED_CHANNEL_GREEN, green);
    if (ret != 0)
    {
        return ret;
    }

    ret = apply_channel_state(instance, RGB_LED_CHANNEL_BLUE, blue);
    if (ret != 0)
    {
        return ret;
    }

    return RGB_LED_OK;
}

int rgb_led_deinit(rgb_led_instance_t* instance)
{
    uint8_t i;

    /* NULL pointer check */
    if (instance == NULL)
    {
        return RGB_LED_ERR_NULL_PTR;
    }

    /* Turn off all channels */
    for (i = 0; i < RGB_LED_CHANNEL_COUNT; i++)
    {
        (void)apply_channel_state(instance, i, RGB_LED_STATE_OFF);
    }

    instance->initialized = false;
    return RGB_LED_OK;
}
