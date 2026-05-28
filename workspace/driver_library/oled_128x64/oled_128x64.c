/**
 * @file    oled_ch1116.c
 * @brief   Implementation of the CH1116 OLED driver (I2C)
 * 
 * Communication protocol:
 *   - I2C control byte: 0x00 = command stream, 0x40 = data stream.
 *   - All writes are sent as: [I2C address + W] [control byte] [payload bytes...]
 *   - The chip does not support reading via I2C (no data readback).
 * 
 * @note    Refer to CH1116 datasheet for more details on initialization sequence.
 */

#include "oled_ch1116.h"
#include <stddef.h>     /* for NULL */

/* ---------------------------------------------------------------------------
 * Internal helpers
 * ---------------------------------------------------------------------------
 */

/**
 * @brief   Build an I2C message with control byte prefix.
 * @param   hal         HAL structure (assumed non-NULL by caller).
 * @param   ctrl_byte   Control byte: 0x00 for command, 0x40 for data.
 * @param   payload     Payload bytes.
 * @param   len         Number of payload bytes.
 * @return  Result from hal->i2c_write.
 */
static int ch1116_i2c_send(const ch1116_hal_t *hal, uint8_t ctrl_byte,
                           const uint8_t *payload, uint16_t len, uint32_t timeout)
{
    /* We send control byte + payload in one transfer (CH1116 accepts this) */
    /* Build a temporary buffer: ctrl + payload */
    /* Max payload size for typical usage is small, so stack buffer is safe */
    /* For larger buffers (e.g., full frame), caller should use a larger buffer.
       This driver limits writes to a single page (128 bytes) which fits. */
    uint8_t buffer[129];  /* 1 control byte + up to 128 data bytes */
    uint16_t i;

    if (payload == NULL) return CH1116_ERR_NULL;
    if (len > 128) return CH1116_ERR_INVALID;  /* protect buffer size */

    buffer[0] = ctrl_byte;
    for (i = 0; i < len; i++) {
        buffer[i + 1] = payload[i];
    }

    return hal->i2c_write(CH1116_I2C_ADDR, buffer, len + 1, timeout);
}

/* ---------------------------------------------------------------------------
 * Initialization step helpers (reduces complexity of ch1116_init)
 * ---------------------------------------------------------------------------
 */

/**
 * @brief   Send a two-byte command sequence (command byte + argument byte).
 * @param   hal         HAL structure.
 * @param   cmd         Command byte.
 * @param   arg         Argument byte.
 * @return  CH1116_OK on success, or negative error code.
 */
static int ch1116_write_cmd_arg(const ch1116_hal_t *hal, uint8_t cmd, uint8_t arg)
{
    int ret;
    ret = ch1116_write_command(hal, cmd);
    if (ret != CH1116_OK) return ret;
    return ch1116_write_command(hal, arg);
}

/**
 * @brief   Perform basic display configuration steps 1–8 of the init sequence.
 * @param   hal         HAL structure.
 * @return  CH1116_OK on success, or negative error code.
 */
static int ch1116_init_basic_config(const ch1116_hal_t *hal)
{
    int ret;

    /* 1. Display off */
    ret = ch1116_write_command(hal, CH1116_CMD_DISPLAY_OFF);
    if (ret != CH1116_OK) return ret;

    /* 2. Set multiplex ratio to 64 (for 128x64) */
    ret = ch1116_write_cmd_arg(hal, CH1116_CMD_SET_MULTIPLEX_RATIO, 0x3F);
    if (ret != CH1116_OK) return ret;

    /* 3. Set display offset = 0 */
    ret = ch1116_write_cmd_arg(hal, CH1116_CMD_SET_DISPLAY_OFFSET, 0x00);
    if (ret != CH1116_OK) return ret;

    /* 4. Set start line (0) */
    ret = ch1116_write_command(hal, CH1116_CMD_SET_START_LINE | 0x00);
    if (ret != CH1116_OK) return ret;

    /* 5. Segment remap: column 127 mapped to SEG0 (for correct left-to-right) */
    ret = ch1116_write_command(hal, CH1116_CMD_SET_SEGMENT_REMAP);
    if (ret != CH1116_OK) return ret;

    /* 6. COM scan direction: remapped (C8) */
    ret = ch1116_write_command(hal, CH1116_CMD_SET_COM_SCAN_DIR);
    if (ret != CH1116_OK) return ret;

    /* 7. Set COM pins hardware config (for 128x64: alternative config) */
    ret = ch1116_write_cmd_arg(hal, CH1116_CMD_SET_COM_PINS, 0x12);
    if (ret != CH1116_OK) return ret;

    /* 8. Set contrast */
    ret = ch1116_set_contrast(hal, 0x7F);
    if (ret != CH1116_OK) return ret;

    return CH1116_OK;
}

/**
 * @brief   Perform advanced display configuration steps 9–15 of the init sequence.
 * @param   hal         HAL structure.
 * @return  CH1116_OK on success, or negative error code.
 */
static int ch1116_init_advanced_config(const ch1116_hal_t *hal)
{
    int ret;

    /* 9. Disable entire display on (follow ram) */
    ret = ch1116_write_command(hal, 0xA4);
    if (ret != CH1116_OK) return ret;

    /* 10. Set normal display (non-inverted) */
    ret = ch1116_write_command(hal, CH1116_CMD_SET_NORMAL_DISPLAY);
    if (ret != CH1116_OK) return ret;

    /* 11. Set oscillator frequency (divide ratio) */
    ret = ch1116_write_cmd_arg(hal, CH1116_CMD_SET_DISPLAY_CLK_DIV, 0x80);
    if (ret != CH1116_OK) return ret;

    /* 12. Enable charge pump (for internal DC-DC) */
    ret = ch1116_write_cmd_arg(hal, CH1116_CMD_CHARGE_PUMP, 0x14);
    if (ret != CH1116_OK) return ret;

    /* 13. Set pre-charge period */
    ret = ch1116_write_cmd_arg(hal, CH1116_CMD_SET_PRECHARGE, 0xF1);
    if (ret != CH1116_OK) return ret;

    /* 14. Set VCOMH deselect level */
    ret = ch1116_write_cmd_arg(hal, CH1116_CMD_SET_VCOM_DESELECT, 0x30);
    if (ret != CH1116_OK) return ret;

    /* 15. Deactivate scroll */
    ret = ch1116_write_command(hal, CH1116_CMD_DEACTIVATE_SCROLL);
    if (ret != CH1116_OK) return ret;

    return CH1116_OK;
}

/* ---------------------------------------------------------------------------
 * Public API implementation
 * ---------------------------------------------------------------------------
 */

int ch1116_init(const ch1116_hal_t *hal)
{
    int ret;

    if (hal == NULL) return CH1116_ERR_NULL;
    if (hal->i2c_write == NULL) return CH1116_ERR_NULL;
    if (hal->delay_ms == NULL) return CH1116_ERR_NULL;

    /* Standard CH1116 initialization sequence (based on typical SSD1306-like
       startup, adapted for CH1116) */

    /* Steps 1-8: basic display configuration */
    ret = ch1116_init_basic_config(hal);
    if (ret != CH1116_OK) return ret;

    /* Steps 9-15: advanced configuration */
    ret = ch1116_init_advanced_config(hal);
    if (ret != CH1116_OK) return ret;

    /* 16. Clear display (GDDRAM) */
    ret = ch1116_clear_display(hal);
    if (ret != CH1116_OK) return ret;

    /* 17. Display on */
    ret = ch1116_display_on(hal);
    if (ret != CH1116_OK) return ret;

    return CH1116_OK;
}

int ch1116_write_command(const ch1116_hal_t *hal, uint8_t command)
{
    if (hal == NULL) return CH1116_ERR_NULL;
    if (hal->i2c_write == NULL) return CH1116_ERR_NULL;

    /* Control byte = 0x00 for commands */
    return ch1116_i2c_send(hal, 0x00, &command, 1, 10);
}

int ch1116_write_data(const ch1116_hal_t *hal, uint8_t data)
{
    if (hal == NULL) return CH1116_ERR_NULL;
    if (hal->i2c_write == NULL) return CH1116_ERR_NULL;

    /* Control byte = 0x40 for data */
    return ch1116_i2c_send(hal, 0x40, &data, 1, 10);
}

int ch1116_write_buffer(const ch1116_hal_t *hal, uint8_t page, uint8_t column,
                        const uint8_t *buffer, uint16_t length)
{
    int ret;

    if (hal == NULL) return CH1116_ERR_NULL;
    if (hal->i2c_write == NULL) return CH1116_ERR_NULL;
    if (buffer == NULL) return CH1116_ERR_NULL;

    /* Validate page and column range */
    if (page >= CH1116_PAGES) return CH1116_ERR_INVALID;
    if (column >= CH1116_LCD_WIDTH) return CH1116_ERR_INVALID;
    if ((column + length) > CH1116_LCD_WIDTH) return CH1116_ERR_INVALID;

    /* Set page address */
    ret = ch1116_write_command(hal, CH1116_CMD_PAGE_ADDR);
    if (ret != CH1116_OK) return ret;

#if 0
    /* For CH1116, page address is set via command 0xB0 + page */
    ret = ch1116_write_command(hal, 0xB0 | page);
#else
    /* Using new command style */
    ret = ch1116_write_command(hal, 0xB0 | page);
#endif
    if (ret != CH1116_OK) return ret;

    /* Set column address (low nibble + high nibble) */
    ret = ch1116_write_command(hal, 0x00 | (column & 0x0F));
    if (ret != CH1116_OK) return ret;
    ret = ch1116_write_command(hal, 0x10 | ((column >> 4) & 0x0F));
    if (ret != CH1116_OK) return ret;

    /* Write data (control byte 0x40) */
    return ch1116_i2c_send(hal, 0x40, buffer, length, 20);
}

int ch1116_clear_display(const ch1116_hal_t *hal)
{
    int ret;
    uint8_t page, col;
    uint8_t zero[CH1116_LCD_WIDTH];  /* 128 bytes */

    if (hal == NULL) return CH1116_ERR_NULL;
    if (hal->i2c_write == NULL) return CH1116_ERR_NULL;

    /* Fill buffer with zeros */
    for (col = 0; col < CH1116_LCD_WIDTH; col++) {
        zero[col] = 0;
    }

    for (page = 0; page < CH1116_PAGES; page++) {
        ret = ch1116_write_buffer(hal, page, 0, zero, CH1116_LCD_WIDTH);
        if (ret != CH1116_OK) return ret;
    }

    return CH1116_OK;
}

int ch1116_set_contrast(const ch1116_hal_t *hal, uint8_t contrast)
{
    if (hal == NULL) return CH1116_ERR_NULL;
    if (hal->i2c_write == NULL) return CH1116_ERR_NULL;
    return ch1116_write_cmd_arg(hal, CH1116_CMD_SET_CONTRAST, contrast);
}

int ch1116_display_on(const ch1116_hal_t *hal)
{
    if (hal == NULL) return CH1116_ERR_NULL;
    if (hal->i2c_write == NULL) return CH1116_ERR_NULL;
    return ch1116_write_command(hal, CH1116_CMD_DISPLAY_ON);
}

int ch1116_display_off(const ch1116_hal_t *hal)
{
    if (hal == NULL) return CH1116_ERR_NULL;
    if (hal->i2c_write == NULL) return CH1116_ERR_NULL;
    return ch1116_write_command(hal, CH1116_CMD_DISPLAY_OFF);
}
