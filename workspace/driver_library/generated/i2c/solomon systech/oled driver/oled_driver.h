/**
 * @file    ch1116.h
 * @brief   MCU-independent driver for CH1116 OLED controller over I2C.
 *
 * @details Provides initialization, control, and text/graphics primitives.
 *          All I2C operations are performed via a user-supplied callback,
 *          making the driver completely platform-agnostic.
 *
 * @note    This driver assumes the CH1116 module auto-resets on power-up,
 *          so no explicit RST pin management is required.
 *
 * @warning No malloc, free, or printf are used anywhere in the driver.
 *          All public functions return int (0 = success, negative = error).
 */

#ifndef CH1116_H
#define CH1116_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---------------------------------------------------------------------------
 * Public types
 * ------------------------------------------------------------------------- */

/**
 * @brief I2C write function pointer.
 *
 * The user must provide an implementation that writes `len` bytes from `data`
 * to the I2C slave with 7-bit address `dev_addr`.  The function should return
 * 0 on success, or a negative error code on failure.
 *
 * @param[in] dev_addr  7-bit I2C slave address (e.g., 0x3C)
 * @param[in] data      Pointer to the byte(s) to send
 * @param[in] len       Number of bytes to send
 * @return 0 on success, negative on error
 */
typedef int (*ch1116_i2c_write_t)(uint8_t dev_addr, const uint8_t *data, uint16_t len);

/**
 * @brief CH1116 device handle.
 *
 * All state is kept in this structure.  No global variables are used.
 */
typedef struct {
    ch1116_i2c_write_t  i2c_write;  /**< I²C write function (must not be NULL) */
    uint8_t             dev_addr;   /**< 7-bit I²C slave address              */
} ch1116_t;

/* ---------------------------------------------------------------------------
 * Public functions
 * ------------------------------------------------------------------------- */

/**
 * @brief   Initialize the CH1116 OLED controller.
 *
 * Sends the full initialization command sequence over I²C.
 * The device handle must already be filled with a valid i2c_write and address.
 *
 * @param[in,out] dev  Pointer to a ch1116_t handle (must be non-NULL)
 * @return 0 on success, negative error code on failure
 */
int ch1116_init(ch1116_t *dev);

/**
 * @brief   Set display contrast (brightness).
 *
 * @param[in] dev       Pointer to device handle (non-NULL)
 * @param[in] contrast  Contrast value (0..255).  Typical default is 0x7F.
 * @return 0 on success, negative on error
 */
int ch1116_set_contrast(ch1116_t *dev, uint8_t contrast);

/**
 * @brief   Clear the entire display RAM (fill with zeros).
 *
 * @param[in] dev  Pointer to device handle (non-NULL)
 * @return 0 on success, negative on error
 */
int ch1116_clear(ch1116_t *dev);

/**
 * @brief   Set the cursor position to a specific (column, page).
 *
 * Column range is 0..131, page range is 0..7 (each page = 8 rows).
 *
 * @param[in] dev    Pointer to device handle (non-NULL)
 * @param[in] col    Column address (0..131)
 * @param[in] page   Page address (0..7)
 * @return 0 on success, negative on error
 */
int ch1116_set_cursor(ch1116_t *dev, uint8_t col, uint8_t page);

/**
 * @brief   Write a single ASCII character at the current cursor position.
 *
 * Uses internal 5×7 font table (ASCII 0x20..0x7E).  Cursor advances by 6
 * columns after the character (1 column padding).
 *
 * @param[in] dev  Pointer to device handle (non-NULL)
 * @param[in] c    ASCII character to display (0x20..0x7E)
 * @return 0 on success, negative on error
 */
int ch1116_write_char(ch1116_t *dev, char c);

/**
 * @brief   Write a null-terminated string at the current cursor position.
 *
 * Characters outside the printable ASCII range are skipped.
 *
 * @param[in] dev  Pointer to device handle (non-NULL)
 * @param[in] str  Null-terminated string (must be non-NULL)
 * @return 0 on success, negative on error
 */
int ch1116_write_string(ch1116_t *dev, const char *str);

/**
 * @brief   Set or clear a single pixel at (x, y).
 *
 * @param[in] dev    Pointer to device handle (non-NULL)
 * @param[in] x      X coordinate (0..131)
 * @param[in] y      Y coordinate (0..63)
 * @param[in] pixel  Non-zero = set, 0 = clear
 * @return 0 on success, negative on error
 */
int ch1116_draw_pixel(ch1116_t *dev, uint8_t x, uint8_t y, uint8_t pixel);

/**
 * @brief   Turn the display on (resume from sleep).
 *
 * @param[in] dev  Pointer to device handle (non-NULL)
 * @return 0 on success, negative on error
 */
int ch1116_display_on(ch1116_t *dev);

/**
 * @brief   Turn the display off (enter sleep mode).
 *
 * @param[in] dev  Pointer to device handle (non-NULL)
 * @return 0 on success, negative on error
 */
int ch1116_display_off(ch1116_t *dev);

#ifdef __cplusplus
}
#endif

#endif /* CH1116_H */
