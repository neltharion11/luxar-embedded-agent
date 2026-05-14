/**
 * @file    ch1116_oled.c
 * @brief   Implementation of CH1116 OLED driver.
 * @details All I2C operations use injected function pointers. No global
 *          HAL handles, no malloc, no printf.
 */

#include "ch1116_oled.h"
#include <stddef.h>   /* NULL */

/* ---------------------------------------------------------------------------
 *  Local helper: write a single byte with a control prefix (command or data)
 * ------------------------------------------------------------------------- */
static int ch1116_write_with_ctrl(ch1116_hal_t *hal, uint8_t dev_addr,
                                   uint8_t ctrl_byte, const uint8_t *payload,
                                   uint16_t len)
{
    if (hal == NULL || hal->i2c_write == NULL)
        return CH1116_ERR_PARAM;

    /* We need to prepend the control byte. To avoid malloc, we build a
     * temporary buffer on the stack. Maximum burst: full display update uses
     * 1024+1 bytes. Stack allocation of 1025 bytes is acceptable on most MCUs
     * (e.g. STM32 default stack is 1k+). For safety we cap at 256 bytes per
     * call; display_buffer_update uses page-at-a-time transfer.
     * This function is only used for short command/data sequences, so we keep
     * a small on-stack buffer. If len > 255, caller must split.
     */
    if (len > 255)
        return CH1116_ERR_PARAM;  /* conservative limit */

    uint8_t buffer[256];  /* 1 ctrl + up to 255 payload */
    buffer[0] = ctrl_byte;
    uint16_t i;
    for (i = 0; i < len; i++)
        buffer[1 + i] = payload[i];

    return hal->i2c_write(hal->context, dev_addr, buffer, 1 + len);
}

/* ---------------------------------------------------------------------------
 *  Public API
 * ------------------------------------------------------------------------- */

int ch1116_init(ch1116_hal_t *hal, uint8_t dev_addr)
{
    int ret;

    if (hal == NULL || hal->i2c_write == NULL || hal->delay_ms == NULL)
        return CH1116_ERR_PARAM;

    /* Initialisation command sequence (compatible with CH1116 / SSD1306) */
    static const uint8_t init_seq[] = {
        0xAE,       /* Display OFF */
        0xD5, 0x80, /* Set oscillator frequency */
        0xA8, 0x3F, /* Set multiplex ratio to 64 (height) */
        0xD3, 0x00, /* Set display offset to 0 */
        0x40,       /* Set start line to 0 */
        0x8D, 0x14, /* Enable charge pump */
        0x20, 0x02, /* Set memory addressing mode to page addressing */
        0xA1,       /* Segment remap (column 127 mapped to SEG0) */
        0xC8,       /* COM output scan direction remapped */
        0xDA, 0x12, /* COM pins hardware configuration */
        0x81, 0x7F, /* Set contrast to 127 */
        0xD9, 0xF1, /* Set pre-charge period */
        0xDB, 0x40, /* Set VCOMH deselect level */
        0xA4,       /* Resume to RAM content display */
        0xA6,       /* Set normal (non-inverted) display */
        0x2E,       /* Deactivate scrolling */
        0xAF        /* Display ON */
    };

    /* Send each command separately (simple approach) */
    for (size_t i = 0; i < sizeof(init_seq); i++) {
        ret = ch1116_send_command(hal, init_seq[i]);
        if (ret != CH1116_OK)
            return ret;
    }

    return CH1116_OK;
}

int ch1116_send_command(ch1116_hal_t *hal, uint8_t cmd)
{
    if (hal == NULL)
        return CH1116_ERR_PARAM;

    /* Single command: ctrl=0x00, payload=cmd */
    return ch1116_write_with_ctrl(hal, CH1116_I2C_ADDR_DEFAULT,
                                   CH1116_CTRL_CMD, &cmd, 1);
}

int ch1116_send_data(ch1116_hal_t *hal, const uint8_t *data, uint16_t len)
{
    if (hal == NULL || data == NULL)
        return CH1116_ERR_PARAM;

    /* Data: ctrl=0x40, payload=len bytes */
    return ch1116_write_with_ctrl(hal, CH1116_I2C_ADDR_DEFAULT,
                                   CH1116_CTRL_DATA, data, len);
}

int ch1116_display_buffer_update(ch1116_hal_t *hal, const uint8_t *buffer)
{
    int ret;

    if (hal == NULL || buffer == NULL)
        return CH1116_ERR_PARAM;

    /* Set page address range 0..7 (page mode) */
    /* 0x22 – set page start/end address (command pair) */
    ret = ch1116_send_command(hal, 0x22);
    if (ret != CH1116_OK) return ret;
    ret = ch1116_send_command(hal, 0x00); /* start page 0 */
    if (ret != CH1116_OK) return ret;
    ret = ch1116_send_command(hal, 0x07); /* end page 7 */
    if (ret != CH1116_OK) return ret;

    /* Set column address range 0..127 */
    /* 0x21 – set column start/end address (command pair) */
    ret = ch1116_send_command(hal, 0x21);
    if (ret != CH1116_OK) return ret;
    ret = ch1116_send_command(hal, 0x00); /* start column 0 */
    if (ret != CH1116_OK) return ret;
    ret = ch1116_send_command(hal, 0x7F); /* end column 127 */
    if (ret != CH1116_OK) return ret;

    /* Send all 1024 bytes of framebuffer in one go (fits within common I2C
     * buffer limits – if not, split into page-sized chunks).
     * The ch1116_write_with_ctrl can handle up to 255 payload bytes;
     * we need 1024, so we must send page by page (128 bytes each).
     */
    for (uint8_t page = 0; page < CH1116_PAGE_COUNT; page++) {
        const uint8_t *page_data = &buffer[page * CH1116_LCD_WIDTH];
        /* Send control byte + 128 data bytes */
        ret = ch1116_send_data(hal, page_data, CH1116_LCD_WIDTH);
        if (ret != CH1116_OK)
            return ret;
    }

    return CH1116_OK;
}

int ch1116_reset(ch1116_hal_t *hal, uint8_t rst_pin)
{
    int ret;

    if (hal == NULL)
        return CH1116_ERR_PARAM;

    if (hal->gpio_set == NULL || hal->delay_ms == NULL)
        return CH1116_ERR_UNSUPPORTED;

    /* Reset sequence: pull low, wait 1 µs (at least), release, wait */
    ret = hal->gpio_set(hal->context, rst_pin, 0);
    if (ret != 0) return CH1116_ERR_GPIO;

    hal->delay_ms(10);  /* typical reset pulse width 1-10 ms */

    ret = hal->gpio_set(hal->context, rst_pin, 1);
    if (ret != 0) return CH1116_ERR_GPIO;

    hal->delay_ms(100); /* wait for display to power up */

    return CH1116_OK;
}
