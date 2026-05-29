/**
 * @file    oled_ch1116.h
 * @brief   MCU-agnostic driver for CH1116 OLED controller (I2C interface)
 * 
 * This driver provides basic operations for a 128x64 OLED display using 
 * the CH1116 controller over I2C. All hardware dependencies are injected
 * through the ch1116_hal_t structure.
 * 
 * @note    The I2C address is fixed to 0x3C (SA0 = GND). If different,
 *          modify CH1116_I2C_ADDR.
 */

#ifndef OLED_CH1116_H
#define OLED_CH1116_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---------------------------------------------------------------------------
 * Constants
 * ---------------------------------------------------------------------------
 * Typical I2C address for CH1116 (SA0 = GND). For SA0 = VDD use 0x3D.
 */
#define CH1116_I2C_ADDR         0x3C
#define CH1116_LCD_WIDTH        128
#define CH1116_LCD_HEIGHT       64
#define CH1116_PAGES            (CH1116_LCD_HEIGHT / 8)

/**
 * @brief   CH1116 command codes (subset used in this driver)
 */
#define CH1116_CMD_SET_CONTRAST         0x81
#define CH1116_CMD_DISPLAY_ON           0xAF
#define CH1116_CMD_DISPLAY_OFF          0xAE
#define CH1116_CMD_SET_NORMAL_DISPLAY   0xA6
#define CH1116_CMD_SET_INVERSE_DISPLAY  0xA7
#define CH1116_CMD_SET_MULTIPLEX_RATIO  0xA8
#define CH1116_CMD_SET_DISPLAY_OFFSET   0xD3
#define CH1116_CMD_SET_START_LINE       0x40
#define CH1116_CMD_SET_SEGMENT_REMAP    0xA1
#define CH1116_CMD_SET_COM_SCAN_DIR     0xC8
#define CH1116_CMD_SET_DISPLAY_CLK_DIV  0xD5
#define CH1116_CMD_SET_PRECHARGE        0xD9
#define CH1116_CMD_SET_COM_PINS         0xDA
#define CH1116_CMD_SET_VCOM_DESELECT    0xDB
#define CH1116_CMD_CHARGE_PUMP          0x8D
#define CH1116_CMD_MEMORY_MODE          0x20
#define CH1116_CMD_COLUMN_ADDR          0x21
#define CH1116_CMD_PAGE_ADDR            0x22
#define CH1116_CMD_DEACTIVATE_SCROLL    0x2E
#define CH1116_CMD_ACTIVATE_SCROLL      0x2F

/* ---------------------------------------------------------------------------
 * Error codes (negative, nonzero)
 * ---------------------------------------------------------------------------
 */
#define CH1116_OK               0
#define CH1116_ERR_NULL        -1
#define CH1116_ERR_COMM        -2
#define CH1116_ERR_TIMEOUT     -3
#define CH1116_ERR_INVALID     -4

/* ---------------------------------------------------------------------------
 * HAL Abstraction
 * ---------------------------------------------------------------------------
 * The user must provide these functions before calling any driver API.
 */
typedef struct {
    /**
     * @brief   Write a sequence of bytes to the CH1116 over I2C.
     * @param   dev_addr    7-bit I2C device address (left-shifted by 0).
     * @param   data        Pointer to data buffer.
     * @param   len         Number of bytes to write.
     * @param   timeout     Timeout in milliseconds (0 = no timeout).
     * @return  0 on success, negative on error.
     */
    int (*i2c_write)(uint8_t dev_addr, const uint8_t *data, uint16_t len, uint32_t timeout);

    /**
     * @brief   Delay for at least the specified milliseconds.
     * @param   ms  Delay time in milliseconds.
     */
    void (*delay_ms)(uint32_t ms);
} ch1116_hal_t;

/* ---------------------------------------------------------------------------
 * Driver public API
 * ---------------------------------------------------------------------------
 */

/**
 * @brief   Initialize the CH1116 OLED display.
 * @param   hal     Pointer to the HAL structure with populated callbacks.
 * @return  CH1116_OK on success, negative error code otherwise.
 * @note    The display will be turned on after initialization.
 */
int ch1116_init(const ch1116_hal_t *hal);

/**
 * @brief   Send a single command byte to the display.
 * @param   hal         Pointer to the HAL structure.
 * @param   command     Command byte.
 * @return  CH1116_OK on success, negative error code otherwise.
 */
int ch1116_write_command(const ch1116_hal_t *hal, uint8_t command);

/**
 * @brief   Send a single data byte to the display.
 * @param   hal     Pointer to the HAL structure.
 * @param   data    Data byte.
 * @return  CH1116_OK on success, negative error code otherwise.
 */
int ch1116_write_data(const ch1116_hal_t *hal, uint8_t data);

/**
 * @brief   Write a buffer of data to the display (GDDRAM).
 * @param   hal         Pointer to the HAL structure.
 * @param   page        Page (row) address (0 .. CH1116_PAGES-1).
 * @param   column      Column start address (0 .. CH1116_LCD_WIDTH-1).
 * @param   buffer      Pointer to data buffer.
 * @param   length      Number of bytes to write.
 * @return  CH1116_OK on success, negative error code otherwise.
 * @note    This function sets the column and page address before writing.
 */
int ch1116_write_buffer(const ch1116_hal_t *hal, uint8_t page, uint8_t column,
                        const uint8_t *buffer, uint16_t length);

/**
 * @brief   Clear the entire display (fill GDDRAM with zeros).
 * @param   hal     Pointer to the HAL structure.
 * @return  CH1116_OK on success, negative error code otherwise.
 */
int ch1116_clear_display(const ch1116_hal_t *hal);

/**
 * @brief   Set the display contrast (0..255).
 * @param   hal         Pointer to the HAL structure.
 * @param   contrast    Contrast value (0 = min, 255 = max).
 * @return  CH1116_OK on success, negative error code otherwise.
 */
int ch1116_set_contrast(const ch1116_hal_t *hal, uint8_t contrast);

/**
 * @brief   Turn the display on.
 * @param   hal     Pointer to the HAL structure.
 * @return  CH1116_OK on success, negative error code otherwise.
 */
int ch1116_display_on(const ch1116_hal_t *hal);

/**
 * @brief   Turn the display off (sleep mode).
 * @param   hal     Pointer to the HAL structure.
 * @return  CH1116_OK on success, negative error code otherwise.
 */
int ch1116_display_off(const ch1116_hal_t *hal);

#ifdef __cplusplus
}
#endif

#endif /* OLED_CH1116_H */
