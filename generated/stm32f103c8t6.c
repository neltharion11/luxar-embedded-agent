/**
 * @file rgb_led.c
 * @brief Implementation of the RGB LED driver.
 */

#include "rgb_led.h"
#include <stddef.h>   /* for NULL */

/* ---------------------------------------------------------------------------
 * Internal helpers – HSV to RGB conversion (standard algorithm)
 * -------------------------------------------------------------------------
 * Input: h ∈ [0,360], s,v ∈ [0,255]
 * Output: r,g,b ∈ [0,255]
 *
 * The conversion is integer-only (no floating point).
 * -------------------------------------------------------------------------*/
static void hsv_to_rgb(uint16_t h, uint8_t s, uint8_t v,
                       uint8_t *r, uint8_t *g, uint8_t *b)
{
    /* If saturation is zero, output is grey (all equal) */
    if (s == 0U)
    {
        *r = v;
        *g = v;
        *b = v;
        return;
    }

    /* Normalise hue to 0..359, but we keep range 0..360 for simplicity */
    uint16_t region = h / 60U;
    uint16_t remainder = (h % 60U) * 255U / 60U;  /* 0..255 */

    uint8_t p = (uint8_t)((uint32_t)v * (255U - (uint32_t)s) / 255U);
    uint8_t q = (uint8_t)((uint32_t)v * (255U - (uint32_t)s * remainder / 255U) / 255U);
    uint8_t t = (uint8_t)((uint32_t)v * (255U - (uint32_t)s * (255U - remainder) / 255U) / 255U);

    switch (region)
    {
        case 0U:  *r = v; *g = t; *b = p; break;
        case 1U:  *r = q; *g = v; *b = p; break;
        case 2U:  *r = p; *g = v; *b = t; break;
        case 3U:  *r = p; *g = q; *b = v; break;
        case 4U:  *r = t; *g = p; *b = v; break;
        default:  *r = v; *g = p; *b = q; break;   /* region 5 */
    }
}

/* ---------------------------------------------------------------------------
 * Scale an 8-bit value (0..255) to the configured PWM period (0..period)
 * -------------------------------------------------------------------------*/
static uint32_t scale_to_period(uint32_t value, uint32_t period)
{
    /* value ∈ [0,255], period typically ≤ 65535 */
    return (value * period + 127U) / 255U;
}

/* ---------------------------------------------------------------------------
 * Public API implementation
 * -------------------------------------------------------------------------*/

int rgb_led_init(rgb_led_t *led,
                 const rgb_led_pwm_t *pwm,
                 uint32_t chan_r,
                 uint32_t chan_g,
                 uint32_t chan_b,
                 uint32_t period)
{
    int ret;

    /* Check parameters */
    if ((led == NULL) || (pwm == NULL) || (period == 0U))
    {
        return RGB_LED_ERR_NULL_PTR;
    }

    /* Store interface and config */
    led->pwm      = pwm;
    led->chan_r   = chan_r;
    led->chan_g   = chan_g;
    led->chan_b   = chan_b;
    led->period   = period;

    /* Start with all off */
    led->curr_r   = 0U;
    led->curr_g   = 0U;
    led->curr_b   = 0U;
    led->rainbow_hue = 0U;

    /* Initialise PWM channels */
    ret = pwm->init(chan_r, period);
    if (ret != 0) { return RGB_LED_ERR_PWM_FAIL; }

    ret = pwm->init(chan_g, period);
    if (ret != 0)
    {
        (void)pwm->deinit(chan_r);
        return RGB_LED_ERR_PWM_FAIL;
    }

    ret = pwm->init(chan_b, period);
    if (ret != 0)
    {
        (void)pwm->deinit(chan_r);
        (void)pwm->deinit(chan_g);
        return RGB_LED_ERR_PWM_FAIL;
    }

    /* Apply initial off-state */
    (void)pwm->set_duty(chan_r, 0U);
    (void)pwm->set_duty(chan_g, 0U);
    (void)pwm->set_duty(chan_b, 0U);

    return RGB_LED_OK;
}

int rgb_led_set_rgb(rgb_led_t *led, uint8_t r, uint8_t g, uint8_t b)
{
    int ret;

    if ((led == NULL) || (led->pwm == NULL))
    {
        return RGB_LED_ERR_NULL_PTR;
    }

    uint32_t dr = scale_to_period((uint32_t)r, led->period);
    uint32_t dg = scale_to_period((uint32_t)g, led->period);
    uint32_t db = scale_to_period((uint32_t)b, led->period);

    ret = led->pwm->set_duty(led->chan_r, dr);
    if (ret != 0) { return RGB_LED_ERR_PWM_FAIL; }

    ret = led->pwm->set_duty(led->chan_g, dg);
    if (ret != 0) { return RGB_LED_ERR_PWM_FAIL; }

    ret = led->pwm->set_duty(led->chan_b, db);
    if (ret != 0) { return RGB_LED_ERR_PWM_FAIL; }

    led->curr_r = dr;
    led->curr_g = dg;
    led->curr_b = db;

    return RGB_LED_OK;
}

int rgb_led_set_hsv(rgb_led_t *led, uint16_t h, uint8_t s, uint8_t v)
{
    uint8_t r, g, b;

    if ((led == NULL) || (led->pwm == NULL))
    {
        return RGB_LED_ERR_NULL_PTR;
    }

    /* Clamp hue to 0..360 */
    if (h > 360U)
    {
        h = 360U;
    }

    hsv_to_rgb(h, s, v, &r, &g, &b);
    return rgb_led_set_rgb(led, r, g, b);
}

int rgb_led_rainbow_step(rgb_led_t *led)
{
    int ret;

    if ((led == NULL) || (led->pwm == NULL))
    {
        return RGB_LED_ERR_NULL_PTR;
    }

    /* Increment hue, wrap at 360 */
    led->rainbow_hue++;
    if (led->rainbow_hue > 360U)
    {
        led->rainbow_hue = 0U;
    }

    ret = rgb_led_set_hsv(led, led->rainbow_hue, 255U, 255U);
    return ret;  /* pass-through error */
}

int rgb_led_off(rgb_led_t *led)
{
    return rgb_led_set_rgb(led, 0, 0, 0);
}

int rgb_led_deinit(rgb_led_t *led)
{
    int ret;

    if ((led == NULL) || (led->pwm == NULL))
    {
        return RGB_LED_ERR_NULL_PTR;
    }

    /* Turn off */
    (void)rgb_led_off(led);

    /* De-initialise channels */
    ret = led->pwm->deinit(led->chan_r);
    if (ret != 0) { return RGB_LED_ERR_PWM_FAIL; }

    ret = led->pwm->deinit(led->chan_g);
    if (ret != 0) { return RGB_LED_ERR_PWM_FAIL; }

    ret = led->pwm->deinit(led->chan_b);
    if (ret != 0) { return RGB_LED_ERR_PWM_FAIL; }

    return RGB_LED_OK;
}
