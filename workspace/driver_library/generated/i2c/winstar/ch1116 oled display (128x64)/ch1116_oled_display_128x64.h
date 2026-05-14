#ifndef CH1116_I2C_DRIVER_H
#define CH1116_I2C_DRIVER_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief HAL abstraction for I2C operations.
 */
typedef struct {
    /**
     * @brief Write data to an I2C slave device.
     * @param dev_addr 7-bit I2C slave address (shifted left 1).
     * @param data     Pointer to data buffer.
     * @param len      Number of bytes to write.
     * @return 0 on success, negative error code on failure.
     */
    int (*i2c_write)(uint8_t dev_addr, const uint8_t *data, uint16_t len);
} ch1116_hal_t;

/**
 * @brief CH1116 OLED driver instance.
 */
typedef struct {
    ch1116_hal_t *hal;          /**< HAL interface pointer (must not be NULL) */
    uint8_t       i2c_addr;     /**< 7-bit I2C address (e.g., 0x3C) */
    uint8_t       current_col;  /**< Current column position (0..131) */
    uint8_t       current_page; /**< Current page position (0..7) */
} ch1116_t;

/* ---------------------------------------------------------------------------
 * Public API
 * -------------------------------------------------------------------------*/

/**
 * @brief Initialize the CH1116 OLED display.
 * @param oled     Pointer to CH1116 instance (must be valid).
 * @param hal      Pointer to HAL interface (must be valid).
 * @param i2c_addr 7-bit I2C slave address.
 * @return 0 on success, negative error code on failure.
 */
int ch1116_init(ch1116_t *oled, ch1116_hal_t *hal, uint8_t i2c_addr);

/**
 * @brief Send a single command byte to the OLED controller.
 * @param oled Pointer to CH1116 instance.
 * @param cmd  Command byte.
 * @return 0 on success, negative error code on failure.
 */
int ch1116_send_command(ch1116_t *oled, uint8_t cmd);

/**
 * @brief Send a single data byte to the OLED controller.
 * @param oled Pointer to CH1116 instance.
 * @param data Data byte.
 * @return 0 on success, negative error code on failure.
 */
int ch1116_send_data(ch1116_t *oled, uint8_t data);

/**
 * @brief Set the display cursor to a specific column and page.
 * @param oled Pointer to CH1116 instance.
 * @param col  Column address (0..131).
 * @param page Page address (0..7).
 * @return 0 on success, negative error code on failure.
 */
int ch1116_set_cursor(ch1116_t *oled, uint8_t col, uint8_t page);

/**
 * @brief Clear the entire display (fill with zeros).
 * @param oled Pointer to CH1116 instance.
 * @return 0 on success, negative error code on failure.
 */
int ch1116_clear_display(ch1116_t *oled);

/**
 * @brief Display a null-terminated string at the current cursor position.
 * @note Uses an internal 5x7 pixel font. Only ASCII characters 0x20..0x7E
 *       are supported. The cursor advances by (5 * strlen(str)) columns.
 * @param oled Pointer to CH1116 instance.
 * @param str  Null-terminated string to display.
 * @return 0 on success, negative error code on failure.
 */
int ch1116_display_string(ch1116_t *oled, const char *str);

/**
 * @brief Perform a software reset of the CH1116 controller.
 * @param oled Pointer to CH1116 instance.
 * @return 0 on success, negative error code on failure.
 */
int ch1116_software_reset(ch1116_t *oled);

/**
 * @brief Deinitialize the OLED (turn off display, release resources).
 * @param oled Pointer to CH1116 instance.
 * @return 0 on success.
 */
int ch1116_deinit(ch1116_t *oled);

#ifdef __cplusplus
}
#endif

#endif /* CH1116_I2C_DRIVER_H */
