/**
 * @file    rgb_led_red.c
 * @brief   Implementation of the MCU-agnostic RGB LED RED channel driver.
 *
 * This driver uses bare-metal register access via injected function pointers
 * and register block pointers. No HAL handles are used.
 *
 * @note    The GPIO configuration assumes the STM32F1 register layout.
 *          For other MCUs, the user must adapt the register struct and
 *          configuration constants in the header.
 */

#include "rgb_led_red.h"

/* ---------------------------------------------------------------------------
 * Internal helper: set the output data register bit for the RED channel pin
 * ---------------------------------------------------------------------------
 * Handles polarity: if active-low, a '1' in the bit position turns the LED
 * off, and a '0' turns it on.
 */

static inline void _set_pin_state(const rgb_led_red_config_t *config, uint8_t state)
{
    /* state: 1 = on, 0 = off */
    uint8_t effective;
    if (config->polarity == RGB_LED_RED_POLARITY_ACTIVE_LOW) {
        effective = (state == 1U) ? 0U : 1U;
    } else {
        effective = state;
    }

    if (effective != 0U) {
        /* Use BSRR to set the bit (write 1 to BSx) */
        config->gpio_port->BSRR = (1UL << config->pin);
    } else {
        /* Use BRR to reset the bit (write 1 to BRx) */
        config->gpio_port->BRR = (1UL << config->pin);
    }
}

/* ---------------------------------------------------------------------------
 * Internal helper: configure a GPIO pin as push-pull output
 * ---------------------------------------------------------------------------
 * For STM32F1, the configuration is in CRL (pins 0-7) or CRH (pins 8-15).
 * Each pin uses 4 bits: MODE[1:0] and CNF[1:0].
 */

static int _configure_pin_output(gpio_regs_t *gpio, uint8_t pin)
{
    volatile uint32_t *reg;
    uint32_t shift;

    if (pin < 8U) {
        reg = &gpio->CRL;
        shift = pin * 4U;
    } else {
        reg = &gpio->CRH;
        shift = (pin - 8U) * 4U;
    }

    /* Clear the 4-bit field for this pin */
    *reg &= ~(0x0FUL << shift);

    /* Set MODE = 0b10 (output 2 MHz), CNF = 0b00 (push-pull) */
    *reg |= ( (GPIO_MODE_OUTPUT_2MHZ << 0) | (GPIO_CNF_OUTPUT_PP << 2) ) << shift;

    return RGB_LED_RED_OK;
}

/* ---------------------------------------------------------------------------
 * Internal helper: reset a GPIO pin to default (input, floating)
 * ---------------------------------------------------------------------------
 */

static int _reset_pin(gpio_regs_t *gpio, uint8_t pin)
{
    volatile uint32_t *reg;
    uint32_t shift;

    if (pin < 8U) {
        reg = &gpio->CRL;
        shift = pin * 4U;
    } else {
        reg = &gpio->CRH;
        shift = (pin - 8U) * 4U;
    }

    /* Clear the 4-bit field: MODE=0b00 (input), CNF=0b00 (analog/floating) */
    *reg &= ~(0x0FUL << shift);

    return RGB_LED_RED_OK;
}

/* ---------------------------------------------------------------------------
 * Public API implementations
 * ---------------------------------------------------------------------------
 */

int rgb_led_red_init(const rgb_led_red_config_t *config)
{
    /* NULL checks */
    if (config == NULL) {
        return RGB_LED_RED_ERR_NULL_PTR;
    }
    if (config->gpio_port == NULL) {
        return RGB_LED_RED_ERR_NULL_PTR;
    }
    if (config->rcc == NULL) {
        return RGB_LED_RED_ERR_NULL_PTR;
    }
    if (config->delay_ms == NULL) {
        return RGB_LED_RED_ERR_NULL_PTR;
    }

    /* Pin range check */
    if (config->pin > 15U) {
        return RGB_LED_RED_ERR_INVALID_PIN;
    }

    /* Enable GPIO peripheral clock */
    /* Determine which port based on the gpio_port pointer address.
     * This is a simple heuristic for STM32F1 where GPIO base addresses
     * are: A=0x40010800, B=0x40010C00, C=0x40011000, D=0x40011400, E=0x40011800.
     * For other MCUs, the user must set the correct enable bit manually
     * or provide an alternative mechanism.
     */
    {
        uint32_t base = (uint32_t)(void*)config->gpio_port;
        uint32_t enr_bit = 0;

        /* STM32F1 GPIO base addresses */
        if (base == 0x40010800UL) {
            enr_bit = RCC_APB2ENR_IOPA_EN;
        } else if (base == 0x40010C00UL) {
            enr_bit = RCC_APB2ENR_IOPB_EN;
        } else if (base == 0x40011000UL) {
            enr_bit = RCC_APB2ENR_IOPC_EN;
        } else if (base == 0x40011400UL) {
            enr_bit = RCC_APB2ENR_IOPD_EN;
        } else if (base == 0x40011800UL) {
            enr_bit = RCC_APB2ENR_IOPE_EN;
        } else {
            /* Unknown port: cannot enable clock automatically.
             * Assume the user has already enabled it, or return error.
             * Returning error is safer.
             */
            return RGB_LED_RED_ERR_INVALID_PIN;
        }

        config->rcc->APB2ENR |= enr_bit;

        /* Small delay to ensure the clock is stable (read-back) */
        (void)config->rcc->APB2ENR;
    }

    /* Configure pin as push-pull output */
    int ret = _configure_pin_output(config->gpio_port, config->pin);
    if (ret != RGB_LED_RED_OK) {
        return ret;
    }

    /* Ensure LED starts in OFF state */
    _set_pin_state(config, 0);

    return RGB_LED_RED_OK;
}

int rgb_led_red_on(const rgb_led_red_config_t *config)
{
    if (config == NULL || config->gpio_port == NULL) {
        return RGB_LED_RED_ERR_NULL_PTR;
    }

    _set_pin_state(config, 1);

    return RGB_LED_RED_OK;
}

int rgb_led_red_off(const rgb_led_red_config_t *config)
{
    if (config == NULL || config->gpio_port == NULL) {
        return RGB_LED_RED_ERR_NULL_PTR;
    }

    _set_pin_state(config, 0);

    return RGB_LED_RED_OK;
}

int rgb_led_red_toggle(const rgb_led_red_config_t *config)
{
    if (config == NULL || config->gpio_port == NULL) {
        return RGB_LED_RED_ERR_NULL_PTR;
    }

    /* Read current state from ODR */
    uint32_t current = config->gpio_port->ODR;
    uint8_t state = (current & (1UL << config->pin)) ? 1U : 0U;

    /* Toggle: if on, turn off; if off, turn on */
    _set_pin_state(config, (state == 0U) ? 1U : 0U);

    return RGB_LED_RED_OK;
}

int rgb_led_red_blink_once(const rgb_led_red_config_t *config)
{
    if (config == NULL || config->delay_ms == NULL) {
        return RGB_LED_RED_ERR_NULL_PTR;
    }

    int ret;

    /* Turn on */
    ret = rgb_led_red_on(config);
    if (ret != RGB_LED_RED_OK) {
        return ret;
    }

    /* Wait 500ms */
    ret = config->delay_ms(500U);
    if (ret != 0) {
        return RGB_LED_RED_ERR_TIMING;
    }

    /* Turn off */
    ret = rgb_led_red_off(config);
    if (ret != RGB_LED_RED_OK) {
        return ret;
    }

    /* Wait 500ms */
    ret = config->delay_ms(500U);
    if (ret != 0) {
        return RGB_LED_RED_ERR_TIMING;
    }

    return RGB_LED_RED_OK;
}

int rgb_led_red_deinit(const rgb_led_red_config_t *config)
{
    if (config == NULL || config->gpio_port == NULL) {
        return RGB_LED_RED_ERR_NULL_PTR;
    }

    /* Reset pin to default (input, floating) */
    int ret = _reset_pin(config->gpio_port, config->pin);
    if (ret != RGB_LED_RED_OK) {
        return ret;
    }

    /* Note: We do NOT disable the GPIO clock here, as other peripherals
     * on the same port may still be in use.
     */

    return RGB_LED_RED_OK;
}
