/**
 * @file    rgb_led_red.h
 * @brief   MCU-agnostic driver for the RED channel of an RGB LED.
 *          Controls a single GPIO pin to produce a 1Hz blink pattern
 *          (500ms on, 500ms off) using bare-metal register access.
 *
 * @note    This driver requires the user to provide:
 *          - The GPIO port base address and pin number for the RED channel.
 *          - The LED polarity (common-anode or common-cathode).
 *          - A SysTick-based delay function pointer for timing.
 *
 * @note    CONFIG TODO: The exact GPIO pin for the RGB LED RED channel
 *          is not specified in the board documentation. The user must
 *          determine this (e.g., trace PCB, check manual, or assume a
 *          default like PB0) and provide it via the configuration struct.
 *
 * @note    CONFIG TODO: Verify if the RGB LED is common-anode (active-low)
 *          or common-cathode (active-high) to set the correct polarity.
 */

#ifndef RGB_LED_RED_H
#define RGB_LED_RED_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---------------------------------------------------------------------------
 * Error codes
 * ---------------------------------------------------------------------------
 * All public functions return int. 0 = success, negative = error.
 */

#define RGB_LED_RED_OK              0
#define RGB_LED_RED_ERR_NULL_PTR   -1
#define RGB_LED_RED_ERR_INVALID_PIN -2
#define RGB_LED_RED_ERR_TIMING     -3

/* ---------------------------------------------------------------------------
 * GPIO register access abstraction
 * ---------------------------------------------------------------------------
 * The user must provide a pointer to a struct that maps to the GPIO
 * register block of the port used for the RED channel.
 *
 * This struct assumes the standard STM32F1 GPIO register layout.
 * For other MCUs, the user must provide a compatible struct or adapt
 * the register offsets.
 */

typedef struct {
    volatile uint32_t CRL;      /**< Port configuration register low  (offset 0x00) */
    volatile uint32_t CRH;      /**< Port configuration register high (offset 0x04) */
    volatile uint32_t IDR;      /**< Port input data register         (offset 0x08) */
    volatile uint32_t ODR;      /**< Port output data register        (offset 0x0C) */
    volatile uint32_t BSRR;     /**< Port bit set/reset register      (offset 0x10) */
    volatile uint32_t BRR;      /**< Port bit reset register          (offset 0x14) */
    volatile uint32_t LCKR;     /**< Port configuration lock register (offset 0x18) */
} gpio_regs_t;

/* ---------------------------------------------------------------------------
 * RCC register access abstraction
 * ---------------------------------------------------------------------------
 * The user must provide a pointer to the RCC register block to enable
 * the GPIO peripheral clock.
 */

typedef struct {
    volatile uint32_t CR;       /**< Clock control register            (offset 0x00) */
    volatile uint32_t CFGR;     /**< Clock configuration register      (offset 0x04) */
    volatile uint32_t CIR;      /**< Clock interrupt register          (offset 0x08) */
    volatile uint32_t APB2RSTR; /**< APB2 peripheral reset register    (offset 0x0C) */
    volatile uint32_t APB1RSTR; /**< APB1 peripheral reset register    (offset 0x10) */
    volatile uint32_t AHBENR;   /**< AHB peripheral clock enable register (offset 0x14) */
    volatile uint32_t APB2ENR;  /**< APB2 peripheral clock enable register (offset 0x18) */
    volatile uint32_t APB1ENR;  /**< APB1 peripheral clock enable register (offset 0x1C) */
    volatile uint32_t BDCR;     /**< Backup domain control register    (offset 0x20) */
    volatile uint32_t CSR;      /**< Control/status register           (offset 0x24) */
} rcc_regs_t;

/* ---------------------------------------------------------------------------
 * GPIO pin configuration constants
 * ---------------------------------------------------------------------------
 * These match the STM32F1 GPIO CRL/CRH register bit fields.
 * For other MCUs, the user must provide equivalent values.
 */

/** GPIO mode: input */
#define GPIO_MODE_INPUT         0x0U
/** GPIO mode: output, max speed 10 MHz */
#define GPIO_MODE_OUTPUT_10MHZ  0x1U
/** GPIO mode: output, max speed 2 MHz */
#define GPIO_MODE_OUTPUT_2MHZ   0x2U
/** GPIO mode: output, max speed 50 MHz */
#define GPIO_MODE_OUTPUT_50MHZ  0x3U

/** GPIO output type: push-pull */
#define GPIO_CNF_OUTPUT_PP      0x0U
/** GPIO output type: open-drain */
#define GPIO_CNF_OUTPUT_OD      0x1U
/** GPIO output type: alternate function push-pull */
#define GPIO_CNF_AF_PP          0x2U
/** GPIO output type: alternate function open-drain */
#define GPIO_CNF_AF_OD          0x3U

/* ---------------------------------------------------------------------------
 * RCC clock enable bit masks (APB2ENR)
 * ---------------------------------------------------------------------------
 * These are for STM32F1. For other MCUs, the user must provide the correct
 * bit position for the GPIO port used.
 */

#define RCC_APB2ENR_IOPA_EN     (1U << 2)   /**< GPIOA clock enable */
#define RCC_APB2ENR_IOPB_EN     (1U << 3)   /**< GPIOB clock enable */
#define RCC_APB2ENR_IOPC_EN     (1U << 4)   /**< GPIOC clock enable */
#define RCC_APB2ENR_IOPD_EN     (1U << 5)   /**< GPIOD clock enable */
#define RCC_APB2ENR_IOPE_EN     (1U << 6)   /**< GPIOE clock enable */

/* ---------------------------------------------------------------------------
 * LED polarity
 * ---------------------------------------------------------------------------
 */

/** LED is common-cathode: GPIO high = LED on, GPIO low = LED off */
#define RGB_LED_RED_POLARITY_ACTIVE_HIGH  0U
/** LED is common-anode: GPIO low = LED on, GPIO high = LED off */
#define RGB_LED_RED_POLARITY_ACTIVE_LOW   1U

/* ---------------------------------------------------------------------------
 * Driver configuration structure
 * ---------------------------------------------------------------------------
 * The user must populate this struct before calling rgb_led_red_init().
 */

typedef struct {
    /** Pointer to the GPIO register block for the RED channel port */
    gpio_regs_t *gpio_port;

    /** Pointer to the RCC register block */
    rcc_regs_t  *rcc;

    /** Pin number (0..15) for the RED channel */
    uint8_t      pin;

    /** LED polarity: RGB_LED_RED_POLARITY_ACTIVE_HIGH or _ACTIVE_LOW */
    uint8_t      polarity;

    /**
     * @brief   Delay function pointer.
     * @param   ms  Delay duration in milliseconds.
     * @return  0 on success, negative on error.
     *
     * The user must provide a blocking delay implementation (e.g., using
     * SysTick). This function will be called to generate the 500ms on/off
     * intervals.
     */
    int (*delay_ms)(uint32_t ms);
} rgb_led_red_config_t;

/* ---------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------------
 */

/**
 * @brief   Initialize the RED channel GPIO pin.
 *
 * This function enables the GPIO peripheral clock, configures the pin as
 * a push-pull output at low speed (2 MHz), and ensures the LED starts in
 * the OFF state.
 *
 * @param   config  Pointer to a valid configuration structure.
 * @return  RGB_LED_RED_OK on success, negative error code on failure.
 *
 * @retval  RGB_LED_RED_OK             Initialization successful.
 * @retval  RGB_LED_RED_ERR_NULL_PTR   config, gpio_port, rcc, or delay_ms is NULL.
 * @retval  RGB_LED_RED_ERR_INVALID_PIN pin is > 15.
 */
int rgb_led_red_init(const rgb_led_red_config_t *config);

/**
 * @brief   Turn the RED channel LED on.
 *
 * @param   config  Pointer to a valid configuration structure.
 * @return  RGB_LED_RED_OK on success, negative error code on failure.
 *
 * @retval  RGB_LED_RED_OK             LED turned on.
 * @retval  RGB_LED_RED_ERR_NULL_PTR   config or gpio_port is NULL.
 */
int rgb_led_red_on(const rgb_led_red_config_t *config);

/**
 * @brief   Turn the RED channel LED off.
 *
 * @param   config  Pointer to a valid configuration structure.
 * @return  RGB_LED_RED_OK on success, negative error code on failure.
 *
 * @retval  RGB_LED_RED_OK             LED turned off.
 * @retval  RGB_LED_RED_ERR_NULL_PTR   config or gpio_port is NULL.
 */
int rgb_led_red_off(const rgb_led_red_config_t *config);

/**
 * @brief   Toggle the RED channel LED state.
 *
 * @param   config  Pointer to a valid configuration structure.
 * @return  RGB_LED_RED_OK on success, negative error code on failure.
 *
 * @retval  RGB_LED_RED_OK             LED toggled.
 * @retval  RGB_LED_RED_ERR_NULL_PTR   config or gpio_port is NULL.
 */
int rgb_led_red_toggle(const rgb_led_red_config_t *config);

/**
 * @brief   Run the 1Hz blink cycle once (500ms on, 500ms off).
 *
 * This is a convenience function that calls on(), delay(500), off(),
 * delay(500). It is blocking.
 *
 * @param   config  Pointer to a valid configuration structure.
 * @return  RGB_LED_RED_OK on success, negative error code on failure.
 *
 * @retval  RGB_LED_RED_OK             Blink cycle completed.
 * @retval  RGB_LED_RED_ERR_NULL_PTR   config or delay_ms is NULL.
 * @retval  RGB_LED_RED_ERR_TIMING     delay_ms returned an error.
 */
int rgb_led_red_blink_once(const rgb_led_red_config_t *config);

/**
 * @brief   Deinitialize the RED channel GPIO pin.
 *
 * This function resets the pin to its default state (input, no pull).
 * The GPIO peripheral clock is NOT disabled (other peripherals may use it).
 *
 * @param   config  Pointer to a valid configuration structure.
 * @return  RGB_LED_RED_OK on success, negative error code on failure.
 *
 * @retval  RGB_LED_RED_OK             Deinitialization successful.
 * @retval  RGB_LED_RED_ERR_NULL_PTR   config or gpio_port is NULL.
 */
int rgb_led_red_deinit(const rgb_led_red_config_t *config);

#ifdef __cplusplus
}
#endif

#endif /* RGB_LED_RED_H */
