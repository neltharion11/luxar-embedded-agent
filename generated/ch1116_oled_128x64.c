/**
 * @file ch1116.c
 * @brief Implementation of CH1116 OLED driver (I2C)
 *
 * All I<sup>2</sup>C writes go through the injected HAL interface.
 * No global HAL handles, no malloc/free, no printf.
 */

#include "ch1116.h"

/* -------------------------------------------------------------------------- *
 *  Internal helper: write a command sequence (prepended with control byte)
 * -------------------------------------------------------------------------- */
/**
 * @brief Write multiple command bytes to the OLED.
 * @param dev  Device instance
 * @param cmds Pointer to command buffer
 * @param len  Number of command bytes
 * @return 0 on success, negative error code
 * @note The control byte 0x00 is automatically prepended.
 *       Maximum command sequence length is 64 bytes (enough for init).
 */
static int write_command_multi(ch1116_t *dev, const uint8_t *cmds, uint16_t len)
{
    if ((dev == NULL) || (cmds == NULL) || (len == 0U)) {
        return -1;
    }

    /* Stack buffer for control byte + command bytes – safe for typical use */
    uint8_t buf[64];
    if (len > sizeof(buf) - 1U) {
        return -2; /* sequence too long */
    }

    buf[0] = 0x00;                   /* command mode control byte */
    for (uint16_t i = 0U; i < len; i++) {
        buf[1U + i] = cmds[i];
    }

    return dev->hal.i2c_write(dev->i2c_addr, buf, (uint16_t)(1U + len));
}

/* -------------------------------------------------------------------------- *
 *  Public interface
 * -------------------------------------------------------------------------- */

int ch1116_init(ch1116_t *dev)
{
    if (dev == NULL) {
        return -1;
    }

    /* Recommended initialization sequence for 128x64 (from datasheet) */
    static const uint8_t init_seq[] = {
        0xAE,       /* display off */
        0xD5, 0x80, /* set display clock divide */
        0xA8, 0x3F, /* set multiplex ratio (64 rows) */
        0xD3, 0x00, /* set display offset */
        0x40,       /* set start line */
        0x8D, 0x14, /* charge pump enable */
        0x20, 0x00, /* set memory mode to page mode */
        0xA1,       /* segment remap (column 127 mapped to SEG0) */
        0xC8,       /* COM scan direction (reverse) */
        0xDA, 0x12, /* set COM pins hardware configuration */
        0x81, 0x7F, /* set contrast */
        0xD9, 0xF1, /* set pre‑charge period */
        0xDB, 0x40, /* set VCOMH deselect level */
        0xA4,       /* display all on resume (normal RAM content) */
        0xA6,       /* normal display (non‑inverted) */
        0x2E,       /* deactivate scroll */
        0xAF        /* display on */
    };

    return write_command_multi(dev, init_seq, sizeof(init_seq));
}

int ch1116_send_command(ch1116_t *dev, uint8_t cmd)
{
    if (dev == NULL) {
        return -1;
    }

    uint8_t buf[2] = {0x00, cmd};  /* control byte + command */
    return dev->hal.i2c_write(dev->i2c_addr, buf, 2U);
}

int ch1116_send_data(ch1116_t *dev, uint8_t data)
{
    if (dev == NULL) {
        return -1;
    }

    uint8_t buf[2] = {0x40, data};  /* control byte (data mode) + data */
    return dev->hal.i2c_write(dev->i2c_addr, buf, 2U);
}

int ch1116_write_data_buffer(ch1116_t *dev, const uint8_t *data, uint16_t len)
{
    if ((dev == NULL) || (data == NULL) || (len == 0U)) {
        return -1;
    }

    /* Send data control byte first, then the data payload */
    uint8_t ctrl = 0x40;
    int ret = dev->hal.i2c_write(dev->i2c_addr, &ctrl, 1U);
    if (ret != 0) {
        return ret;
    }

    return dev->hal.i2c_write(dev->i2c_addr, data, len);
}

int ch1116_set_cursor(ch1116_t *dev, uint8_t page, uint8_t col)
{
    if (dev == NULL) {
        return -1;
    }

    if ((page > 7U) || (col > 127U)) {
        return -2; /* invalid coordinate */
    }

    uint8_t cmds[3];
    cmds[0] = (uint8_t)(0xB0 | page);               /* page start address */
    cmds[1] = (uint8_t)(0x00 | (col & 0x0FU));      /* lower column address */
    cmds[2] = (uint8_t)(0x10 | ((col >> 4U) & 0x0FU)); /* higher column address */

    return write_command_multi(dev, cmds, 3U);
}

int ch1116_clear_screen(ch1116_t *dev)
{
    if (dev == NULL) {
        return -1;
    }

    /* Write zeros to all 8 pages, 128 columns each */
    uint8_t zero_row[128] = {0U};
    for (uint8_t page = 0U; page < 8U; page++) {
        int ret = ch1116_set_cursor(dev, page, 0U);
        if (ret != 0) {
            return ret;
        }
        ret = ch1116_write_data_buffer(dev, zero_row, 128U);
        if (ret != 0) {
            return ret;
        }
    }

    return 0;
}

int ch1116_display_on(ch1116_t *dev)
{
    if (dev == NULL) {
        return -1;
    }

    return ch1116_send_command(dev, 0xAF);
}

int ch1116_display_off(ch1116_t *dev)
{
    if (dev == NULL) {
        return -1;
    }

    return ch1116_send_command(dev, 0xAE);
}

int ch1116_deinit(ch1116_t *dev)
{
    if (dev == NULL) {
        return -1;
    }

    /* Put display into sleep mode */
    return ch1116_display_off(dev);
}
