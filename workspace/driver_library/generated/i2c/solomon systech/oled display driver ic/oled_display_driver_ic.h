/**
 * @file    ch1116_oled.h
 * @brief   MCU-agnostic driver for CH1116 OLED display controller over I2C.
 *          All hardware operations are injected via function pointers.
 * @details Supports 128x64 pixel monochrome displays. I2C slave address
 *          is configurable (default 0x3C). An optional reset function
 *          using a GPIO pin is provided but not required for normal operation.
 */

#ifndef CH1116_OLED_H
#define CH1116_OLED_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* --- Error codes --------------------------------------------------------- */
#define CH1116_OK              (0)
#define CH1116_ERR_PARAM       (-1)   /* NULL pointer or invalid argument */
#define CH1116_ERR_I2C         (-2)   /* I2C write transfer failed */
#define CH1116_ERR_GPIO        (-3)   /* GPIO write callback failed */
#define CH1116_ERR_UNSUPPORTED (-4)   /* Feature not available (e.g. optional callback NULL) */

/* --- Default I2C address (write mode) ------------------------------------ */
#define CH1116_I2C_ADDR_DEFAULT (0x3C)

/* --- Display dimensions -------------------------------------------------- */
#define CH1116_LCD_WIDTH    (128)
#define CH1116_LCD_HEIGHT   (64)
#define CH1116_PAGE_COUNT   (8)         /* 64 / 8 */
#define CH1116_BUFFER_SIZE  (CH1116_LCD_WIDTH * CH1116_PAGE_COUNT)  /* 1024 bytes */

/* --- I2C protocol constants ---------------------------------------------- */
#define CH1116_CTRL_CMD    (0x00)   /* Co=0, D/C#=0 */
#define CH1116_CTRL_DATA   (0x40)   /* Co=0, D/C#=1 */

/* --- Driver context (injected HAL operations) ---------------------------- */

/**
 * @brief   Structure holding all hardware abstraction callbacks.
 * @note    All function pointers must be set to valid implementations
 *          before any driver call. If a feature is not required (e.g. reset),
 *          the corresponding pointer can be set to NULL; the driver will
 *          return CH1116_ERR_UNSUPPORTED if that path is taken.
 */
typedef struct {
    void *context;  /**< User-defined context (e.g. I2C handle pointer) */

    /**
     * @brief   Write data to the I2C bus.
     * @param   context   User context pointer.
     * @param   dev_addr  7-bit I2C slave address.
     * @param   data      Pointer to data buffer to transmit.
     * @param   len       Number of bytes to transmit.
     * @retval 0 on success, negative on error.
     */
    int (*i2c_write)(void *context, uint8_t dev_addr, const uint8_t *data, uint16_t len);

    /**
     * @brief   Blocking delay in milliseconds.
     * @param   ms   Delay duration in milliseconds.
     */
    void (*delay_ms)(uint32_t ms);

    /**
     * @brief   Set a GPIO pin level (optional, used by reset function).
     * @param   context  User context pointer.
     * @param   pin      Platform-specific GPIO pin identifier (e.g. port+pin).
     * @param   level    0 = low, 1 = high.
     * @retval 0 on success, negative on error.
     * @note    May be NULL if reset feature is not needed.
     */
    int (*gpio_set)(void *context, uint8_t pin, uint8_t level);
} ch1116_hal_t;

/* --- Public API ---------------------------------------------------------- */

/**
 * @brief   Initialize the CH1116 display with standard configuration.
 * @param   hal       Pointer to valid HAL structure (must not be NULL).
 * @param   dev_addr  7-bit I2C slave address (e.g. CH1116_I2C_ADDR_DEFAULT).
 * @retval  CH1116_OK on success.
 * @retval  CH1116_ERR_PARAM if hal is NULL.
 * @retval  CH1116_ERR_I2C if any I2C transfer fails.
 */
int ch1116_init(ch1116_hal_t *hal, uint8_t dev_addr);

/**
 * @brief   Send a single command byte to the CH1116.
 * @param   hal     Pointer to valid HAL structure.
 * @param   cmd     Command byte.
 * @retval  CH1116_OK on success.
 * @retval  CH1116_ERR_PARAM if hal is NULL.
 * @retval  CH1116_ERR_I2C if I2C write fails.
 */
int ch1116_send_command(ch1116_hal_t *hal, uint8_t cmd);

/**
 * @brief   Send multiple data bytes to the CH1116 (GRAM update).
 * @param   hal     Pointer to valid HAL structure.
 * @param   data    Pointer to data buffer (must not be NULL).
 * @param   len     Number of bytes to send.
 * @retval  CH1116_OK on success.
 * @retval  CH1116_ERR_PARAM if hal or data is NULL.
 * @retval  CH1116_ERR_I2C if I2C write fails.
 */
int ch1116_send_data(ch1116_hal_t *hal, const uint8_t *data, uint16_t len);

/**
 * @brief   Update the entire OLED display from a framebuffer.
 *          This function sets page and column addresses, then sends all data.
 * @param   hal     Pointer to valid HAL structure.
 * @param   buffer  Pointer to 1024-byte framebuffer (CH1116_BUFFER_SIZE).
 * @retval  CH1116_OK on success.
 * @retval  CH1116_ERR_PARAM if hal or buffer is NULL.
 * @retval  CH1116_ERR_I2C if any I2C transfer fails.
 */
int ch1116_display_buffer_update(ch1116_hal_t *hal, const uint8_t *buffer);

/**
 * @brief   Perform a hardware reset of the CH1116 using a GPIO pin.
 *          The reset sequence pulls the pin low, waits, releases, then waits again.
 * @param   hal      Pointer to valid HAL structure.
 * @param   rst_pin  Platform-specific GPIO pin identifier for the RESET line.
 * @retval  CH1116_OK on success.
 * @retval  CH1116_ERR_PARAM if hal is NULL.
 * @retval  CH1116_ERR_UNSUPPORTED if hal->gpio_set or hal->delay_ms is NULL.
 * @retval  CH1116_ERR_GPIO if gpio_set callback fails.
 * @note    This function is optional; the CH1116 module auto-resets on power-up.
 */
int ch1116_reset(ch1116_hal_t *hal, uint8_t rst_pin);

#ifdef __cplusplus
}
#endif

#endif /* CH1116_OLED_H */
