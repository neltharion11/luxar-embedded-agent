/**
 * @file    rgb_led_red.c
 * @brief   Implementation of the MCU-agnostic RGB LED red channel driver.
 *
 * This file implements the functions declared in rgb_led_red.h. All GPIO
 * interactions are performed through the provided HAL abstraction table,
 * ensuring platform independence.
 */

#include "rgb_led_red.h"

/*----------------------------------------------------------------------------*/
/*  Internal Helper Functions                                                 */
/*----------------------------------------------------------------------------*/

/**
 * @brief   Validate the driver instance and its initialization state.
 * @param[in]   p_led   Pointer to the driver instance.
 * @retval  0                    Instance is valid and initialized.
 * @retval  RGB_LED_RED_ERR_NULL_PTR   Instance pointer is NULL.
 * @retval  RGB_LED_RED_ERR_INIT       Instance is not initialized.
 */
static int32_t validate_instance(const rgb_led_red_t *p_led)
{
    int32_t ret = RGB_LED_RED_OK;

    if (p_led == NULL)
    {
        ret = RGB_LED_RED_ERR_NULL_PTR;
    }
    else if (p_led->hal == NULL)  /* 新增：检查 HAL 表指针 */
    {
        ret = RGB_LED_RED_ERR_NULL_PTR;
    }
    else if (p_led->initialized == 0U)
    {
        ret = RGB_LED_RED_ERR_INIT;
    }
    else
    {
        /* Instance is valid. */
    }

    return ret;
}

/*----------------------------------------------------------------------------*/
/*  Public API Functions                                                      */
/*----------------------------------------------------------------------------*/

int32_t rgb_led_red_init(rgb_led_red_t *p_led,
                         const rgb_led_red_hal_t *p_hal,
                         const rgb_led_red_cfg_t *p_cfg)
{
    int32_t ret = RGB_LED_RED_OK;

    /* Validate input pointers. */
    if ((p_led == NULL) || (p_hal == NULL) || (p_cfg == NULL))
    {
        ret = RGB_LED_RED_ERR_NULL_PTR;
    }
    /* Validate HAL function pointers. */
    else if ((p_hal->gpio_write_pin == NULL) || (p_hal->gpio_set_output_pp == NULL))
    {
        ret = RGB_LED_RED_ERR_NULL_PTR;
    }
    else
    {
        /* Store the HAL table and configuration. */
        p_led->hal = p_hal;
        p_led->cfg = *p_cfg;

        /* Configure the GPIO pin as output push-pull. */
        ret = p_led->hal->gpio_set_output_pp(p_led->cfg.gpio_port,
                                             p_led->cfg.gpio_pin);
        if (ret == RGB_LED_RED_OK)
        {
            p_led->initialized = 1U;
        }
    }

    return ret;
}

int32_t rgb_led_red_on(rgb_led_red_t *p_led)
{
    int32_t ret = RGB_LED_RED_OK;

    /* Early NULL check for pointer parameter. */
    if (p_led == NULL)
    {
        ret = RGB_LED_RED_ERR_NULL_PTR;
    }
    else
    {
        ret = validate_instance(p_led);
    }

    if (ret == RGB_LED_RED_OK)
    {
        /* 此时 p_led 和 p_led->hal 已验证非空 */
        ret = p_led->hal->gpio_write_pin(p_led->cfg.gpio_port,
                                         p_led->cfg.gpio_pin,
                                         (uint8_t)RGB_LED_RED_PIN_SET);
    }
    else
    {
        /* Error already set by validate_instance or NULL check. */
    }

    return ret;
}

int32_t rgb_led_red_off(rgb_led_red_t *p_led)
{
    int32_t ret = RGB_LED_RED_OK;

    /* Early NULL check for pointer parameter. */
    if (p_led == NULL)
    {
        ret = RGB_LED_RED_ERR_NULL_PTR;
    }
    else
    {
        ret = validate_instance(p_led);
    }

    if (ret == RGB_LED_RED_OK)
    {
        /* 此时 p_led 和 p_led->hal 已验证非空 */
        ret = p_led->hal->gpio_write_pin(p_led->cfg.gpio_port,
                                         p_led->cfg.gpio_pin,
                                         (uint8_t)RGB_LED_RED_PIN_RESET);
    }
    else
    {
        /* Error already set by validate_instance or NULL check. */
    }

    return ret;
}
