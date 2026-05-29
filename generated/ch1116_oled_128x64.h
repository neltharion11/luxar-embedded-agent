/**
 * @file ch1116.h
 * @brief MCU-independent driver for CH1116 128x64 OLED controller (I2C)
 *
 * This driver provides an abstract interface to the CH1116 OLED over I2C.
 * All HAL operations are injected via function pointers – no global handles.
 * No malloc, free, or printf is used.
 */

#ifndef CH1116_H
#define CH1116_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** @brief I2C HAL interface required by the driver */
typedef struct {
    /**
     * @brief Write data to an I2C slave.
     * @param dev_addr 8-bit I2C slave address (including R/W bit, e.g., 0x3D or 0x7A)
     * @param data     Pointer to data buffer
     * @param len      Number of bytes to write
     * @return 0 on success, negative error code on failure
     */
    int (*i2c_write)(uint8_t dev_addr, const uint8_t *data, uint16_t len);
} ch1116_hal_t;

/** @brief CH1116 device instance */
typedef struct {
    ch1116_hal_t hal;      /**< Injected HAL interface */
    uint8_t      i2c_addr; /**< I2C write address (e.g., 0x3D or 0x7A) */
} ch1116_t;

/**
 * @brief Initialize the CH1116 OLED driver with the recommended start‑up sequence.
 * @param dev Pointer to an initialized device instance
 * @return 0 on success, negative error code
 */
int ch1116_init(ch1116_t *dev);

/**
 * @brief Send a single command byte to the OLED.
 * @param dev Pointer to device instance
 * @param cmd Command byte
 * @return 0 on success, negative error code
 */
int ch1116_send_command(ch1116_t *dev, uint8_t cmd);

/**
 * @brief Send a single data byte to the OLED.
 * @param dev Pointer to device instance
 * @param data Data byte
 * @return 0 on success, negative error code
 */
int ch1116_send_data(ch1116_t *dev, uint8_t data);

/**
 * @brief Write a buffer of data bytes (e.g., graphics data) to the OLED.
 * @param dev  Pointer to device instance
 * @param data Pointer to data buffer
 * @param len  Number of bytes to write
 * @return 0 on success, negative error code
 */
int ch1116_write_data_buffer(ch1116_t *dev, const uint8_t *data, uint16_t len);

/**
 * @brief Set the cursor position (page and column) for subsequent data writes.
 * @param dev  Pointer to device instance
 * @param page Page address (0–7)
 * @param col  Column address (0–127)
 * @return 0 on success, negative error code
 */
int ch1116_set_cursor(ch1116_t *dev, uint8_t page, uint8_t col);

/**
 * @brief Clear the entire screen (fill all pages with zeros).
 * @param dev Pointer to device instance
 * @return 0 on success, negative error code
 */
int ch1116_clear_screen(ch1116_t *dev);

/**
 * @brief Turn the display on (exit sleep mode).
 * @param dev Pointer to device instance
 * @return 0 on success, negative error code
 */
int ch1116_display_on(ch1116_t *dev);

/**
 * @brief Turn the display off (sleep mode).
 * @param dev Pointer to device instance
 * @return 0 on success, negative error code
 */
int ch1116_display_off(ch1116_t *dev);

/**
 * @brief Deinitialize the OLED (put into low‑power state).
 * @param dev Pointer to device instance
 * @return 0 on success, negative error code
 */
int ch1116_deinit(ch1116_t *dev);

#ifdef __cplusplus
}
#endif

#endif /* CH1116_H */
