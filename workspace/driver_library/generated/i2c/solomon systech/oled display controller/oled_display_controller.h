/**
 * @file    ch1116.h
 * @brief   CH1116 OLED Display Driver (I2C)
 *
 * MCU-independent driver for the CH1116 132x64 OLED controller.
 * All I2C operations are performed via injected function pointers.
 * No global HAL handles, no printf/malloc/free.
 *
 * @version 1.0.0
 * @date    2025-04-11
 */

#ifndef CH1116_H
#define CH1116_H

#include <stdint.h>

/* ========================================================================== */
/*  Configuration Constants                                                   */
/* ========================================================================== */

/**
 * @brief Default I2C device address (7-bit) for CH1116 when SA0 is low.
 */
#define CH1116_I2C_ADDR_SA0_LOW    ((uint8_t)0x3C)

/**
 * @brief Default I2C device address (7-bit) for CH1116 when SA0 is high.
 */
#define CH1116_I2C_ADDR_SA0_HIGH   ((uint8_t)0x3D)

/**
 * @brief Display dimensions (pixels).
 */
#define CH1116_WIDTH               132
#define CH1116_HEIGHT              64
#define CH1116_PAGES               (CH1116_HEIGHT / 8)

/* ========================================================================== */
/*  HAL Abstraction (Platform Adapter)                                         */
/* ========================================================================== */

/**
 * @brief I2C write function signature.
 *
 * The function must write `len` bytes from `data` to the slave device
 * at address `dev_addr` on the I2C bus.
 *
 * @param[in] dev_addr   7-bit I2C slave address.
 * @param[in] data       Pointer to the data to write.
 * @param[in] len        Number of bytes to write.
 * @return int           0 on success, negative error code on failure.
 */
typedef int (*ch1116_i2c_write_fn)(uint8_t dev_addr, const uint8_t *data, uint16_t len);

/**
 * @brief CH1116 HAL context.
 *
 * The user must populate this structure before calling any driver functions.
 *
 * @param[in] dev_addr   7-bit I2C address of the CH1116.
 * @param[in] i2c_write  Function pointer to platform I2C write function.
 */
typedef struct {
    uint8_t            dev_addr;
    ch1116_i2c_write_fn i2c_write;
} ch1116_hal_t;

/* ========================================================================== */
/*  Public API                                                                */
/* ========================================================================== */

/**
 * @brief   Initialize the CH1116 OLED display.
 *
 * Performs the full initialization sequence: sets up internal DC-DC,
 * display timing, addressing mode, and clears the display.
 *
 * @param[in] hal  Pointer to a valid ch1116_hal_t context (must not be NULL).
 * @return int     0 on success, negative error code on failure.
 */
int ch1116_init(ch1116_hal_t *hal);

/**
 * @brief   Send a single command byte to the CH1116.
 *
 * @param[in] hal   Pointer to valid HAL context.
 * @param[in] cmd   Command byte.
 * @return int      0 on success, negative error code on failure.
 */
int ch1116_send_command(ch1116_hal_t *hal, uint8_t cmd);

/**
 * @brief   Send a single data byte to the CH1116 (GDDRAM write).
 *
 * @param[in] hal   Pointer to valid HAL context.
 * @param[in] data  Data byte.
 * @return int      0 on success, negative error code on failure.
 */
int ch1116_send_data(ch1116_hal_t *hal, uint8_t data);

/**
 * @brief   Clear the entire display (fill with 0x00).
 *
 * @param[in] hal  Pointer to valid HAL context.
 * @return int     0 on success, negative error code on failure.
 */
int ch1116_clear(ch1116_hal_t *hal);

/**
 * @brief   Set cursor column and page (row) position.
 *
 * Column range: 0 .. CH1116_WIDTH - 1
 * Page  range:  0 .. CH1116_PAGES - 1
 *
 * @param[in] hal   Pointer to valid HAL context.
 * @param[in] col   Column (X) coordinate.
 * @param[in] page  Page (Y/8) coordinate.
 * @return int      0 on success, negative error code on failure.
 */
int ch1116_set_cursor(ch1116_hal_t *hal, uint8_t col, uint8_t page);

/**
 * @brief   Write a single ASCII character at the current cursor position.
 *
 * The font is 5x7 pixels. After writing, the cursor moves right by 6 pixels
 * (5 + 1 spacing). The character must be printable (0x20..0x7E).
 *
 * @param[in] hal  Pointer to valid HAL context.
 * @param[in] ch   ASCII character to write.
 * @return int     0 on success, negative error code on failure.
 */
int ch1116_write_char(ch1116_hal_t *hal, char ch);

/**
 * @brief   Write a null-terminated string at the current cursor position.
 *
 * @param[in] hal  Pointer to valid HAL context.
 * @param[in] str  Pointer to the string (must not be NULL).
 * @return int     0 on success, negative error code on failure.
 */
int ch1116_write_string(ch1116_hal_t *hal, const char *str);

/**
 * @brief   Write a small positive integer (0..999) as decimal digits at current cursor.
 *
 * @param[in] hal       Pointer to valid HAL context.
 * @param[in] value     Integer value (0..999).
 * @param[in] digits    Number of digits to pad (e.g., 3 for "128").
 * @return int          0 on success, negative error code on failure.
 */
int ch1116_write_uint(ch1116_hal_t *hal, uint16_t value, uint8_t digits);

/**
 * @brief   Dummy reset function (no operation required for CH1116).
 *
 * Included for API completeness. May be used to perform a software reset
 * sequence internally.
 *
 * @param[in] hal  Pointer to valid HAL context.
 * @return int     0 always.
 */
int ch1116_reset(ch1116_hal_t *hal);

#endif /* CH1116_H */
