/**
 * @file    ch1116.c
 * @brief   Implementation of MCU-independent CH1116 OLED driver.
 *
 * @details All I²C transfers are performed through a callback function pointer,
 *          making the driver independent of any specific HAL or platform.
 *
 * @note    Font table: 5×7 bitmapped characters for ASCII 0x20..0x7E.
 *          Each character occupies 5 bytes (columns), MSB top row.
 */

#include "ch1116.h"

#include <stddef.h> /* for NULL */

/* ---------------------------------------------------------------------------
 * Internal helpers
 * ------------------------------------------------------------------------- */

/**
 * @brief Send a single command byte to the CH1116.
 *
 * The I²C write consists of a control byte (0x00) followed by the command.
 *
 * @param dev   Device handle
 * @param cmd   Command byte
 * @return 0 on success, negative on error
 */
static int ch1116_send_command(ch1116_t *dev, uint8_t cmd)
{
    uint8_t buf[2] = {0x00, cmd};
    return dev->i2c_write(dev->dev_addr, buf, 2);
}

/**
 * @brief Send a single data byte to the CH1116.
 *
 * The I²C write consists of a control byte (0x40) followed by the data byte.
 *
 * @param dev   Device handle
 * @param data  Data byte
 * @return 0 on success, negative on error
 */
static int ch1116_send_data_byte(ch1116_t *dev, uint8_t data)
{
    uint8_t buf[2] = {0x40, data};
    return dev->i2c_write(dev->dev_addr, buf, 2);
}

/**
 * @brief Send a block of data bytes to the CH1116.
 *
 * The first byte of the transmitted block is the control byte 0x40,
 * followed by `len` data bytes.
 *
 * @param dev   Device handle
 * @param data  Pointer to the data bytes
 * @param len   Number of data bytes
 * @return 0 on success, negative on error
 */
static int ch1116_send_data_block(ch1116_t *dev, const uint8_t *data, uint16_t len)
{
    /*
     * Build a temporary buffer with control byte + payload.
     * Using a fixed-size stack array for reasonable block sizes.
     * Maximum payload per call is 128 bytes to keep stack usage safe.
     * For larger blocks (e.g., full clear) the loop is done in the caller.
     */
    uint8_t buf[129]; /* 1 control + 128 data */
    uint16_t remaining = len;
    int ret = 0;

    while (remaining > 0) {
        uint16_t chunk = (remaining > 128) ? 128 : remaining;
        buf[0] = 0x40;
        uint16_t i;
        for (i = 0; i < chunk; i++) {
            buf[1 + i] = data[i];
        }
        ret = dev->i2c_write(dev->dev_addr, buf, 1 + chunk);
        if (ret != 0) {
            return ret;
        }
        data      += chunk;
        remaining -= chunk;
    }
    return 0;
}

/* ---------------------------------------------------------------------------
 * Five by seven pixel font table (ASCII 0x20 .. 0x7E)
 * Each character is 5 columns, 7 rows (MSB of byte = top row).
 * ------------------------------------------------------------------------- */
static const uint8_t font_5x7[][5] = {
    /* 0x20 ' ' */ {0x00, 0x00, 0x00, 0x00, 0x00},
    /* 0x21 '!' */ {0x00, 0x00, 0x5F, 0x00, 0x00},
    /* 0x22 '"' */ {0x00, 0x07, 0x00, 0x07, 0x00},
    /* 0x23 '#' */ {0x14, 0x7F, 0x14, 0x7F, 0x14},
    /* 0x24 '$' */ {0x24, 0x2A, 0x7F, 0x2A, 0x12},
    /* 0x25 '%' */ {0x23, 0x13, 0x08, 0x64, 0x62},
    /* 0x26 '&' */ {0x36, 0x49, 0x55, 0x22, 0x50},
    /* 0x27 ''' */ {0x00, 0x05, 0x03, 0x00, 0x00},
    /* 0x28 '(' */ {0x00, 0x1C, 0x22, 0x41, 0x00},
    /* 0x29 ')' */ {0x00, 0x41, 0x22, 0x1C, 0x00},
    /* 0x2A '*' */ {0x08, 0x2A, 0x1C, 0x2A, 0x08},
    /* 0x2B '+' */ {0x08, 0x08, 0x3E, 0x08, 0x08},
    /* 0x2C ',' */ {0x00, 0x50, 0x30, 0x00, 0x00},
    /* 0x2D '-' */ {0x08, 0x08, 0x08, 0x08, 0x08},
    /* 0x2E '.' */ {0x00, 0x60, 0x60, 0x00, 0x00},
    /* 0x2F '/' */ {0x20, 0x10, 0x08, 0x04, 0x02},
    /* 0x30 '0' */ {0x3E, 0x51, 0x49, 0x45, 0x3E},
    /* 0x31 '1' */ {0x00, 0x42, 0x7F, 0x40, 0x00},
    /* 0x32 '2' */ {0x42, 0x61, 0x51, 0x49, 0x46},
    /* 0x33 '3' */ {0x21, 0x41, 0x45, 0x4B, 0x31},
    /* 0x34 '4' */ {0x18, 0x14, 0x12, 0x7F, 0x10},
    /* 0x35 '5' */ {0x27, 0x45, 0x45, 0x45, 0x39},
    /* 0x36 '6' */ {0x3C, 0x4A, 0x49, 0x49, 0x30},
    /* 0x37 '7' */ {0x01, 0x71, 0x09, 0x05, 0x03},
    /* 0x38 '8' */ {0x36, 0x49, 0x49, 0x49, 0x36},
    /* 0x39 '9' */ {0x06, 0x49, 0x49, 0x29, 0x1E},
    /* 0x3A ':' */ {0x00, 0x36, 0x36, 0x00, 0x00},
    /* 0x3B ';' */ {0x00, 0x56, 0x36, 0x00, 0x00},
    /* 0x3C '<' */ {0x00, 0x08, 0x14, 0x22, 0x41},
    /* 0x3D '=' */ {0x14, 0x14, 0x14, 0x14, 0x14},
    /* 0x3E '>' */ {0x41, 0x22, 0x14, 0x08, 0x00},
    /* 0x3F '?' */ {0x02, 0x01, 0x51, 0x09, 0x06},
    /* 0x40 '@' */ {0x32, 0x49, 0x79, 0x41, 0x3E},
    /* 0x41 'A' */ {0x7E, 0x11, 0x11, 0x11, 0x7E},
    /* 0x42 'B' */ {0x7F, 0x49, 0x49, 0x49, 0x36},
    /* 0x43 'C' */ {0x3E, 0x41, 0x41, 0x41, 0x22},
    /* 0x44 'D' */ {0x7F, 0x41, 0x41, 0x22, 0x1C},
    /* 0x45 'E' */ {0x7F, 0x49, 0x49, 0x49, 0x41},
    /* 0x46 'F' */ {0x7F, 0x09, 0x09, 0x01, 0x01},
    /* 0x47 'G' */ {0x3E, 0x41, 0x41, 0x51, 0x32},
    /* 0x48 'H' */ {0x7F, 0x08, 0x08, 0x08, 0x7F},
    /* 0x49 'I' */ {0x00, 0x41, 0x7F, 0x41, 0x00},
    /* 0x4A 'J' */ {0x20, 0x40, 0x41, 0x3F, 0x01},
    /* 0x4B 'K' */ {0x7F, 0x08, 0x14, 0x22, 0x41},
    /* 0x4C 'L' */ {0x7F, 0x40, 0x40, 0x40, 0x40},
    /* 0x4D 'M' */ {0x7F, 0x02, 0x04, 0x02, 0x7F},
    /* 0x4E 'N' */ {0x7F, 0x04, 0x08, 0x10, 0x7F},
    /* 0x4F 'O' */ {0x3E, 0x41, 0x41, 0x41, 0x3E},
    /* 0x50 'P' */ {0x7F, 0x09, 0x09, 0x09, 0x06},
    /* 0x51 'Q' */ {0x3E, 0x41, 0x51, 0x21, 0x5E},
    /* 0x52 'R' */ {0x7F, 0x09, 0x19, 0x29, 0x46},
    /* 0x53 'S' */ {0x46, 0x49, 0x49, 0x49, 0x31},
    /* 0x54 'T' */ {0x01, 0x01, 0x7F, 0x01, 0x01},
    /* 0x55 'U' */ {0x3F, 0x40, 0x40, 0x40, 0x3F},
    /* 0x56 'V' */ {0x1F, 0x20, 0x40, 0x20, 0x1F},
    /* 0x57 'W' */ {0x7F, 0x20, 0x18, 0x20, 0x7F},
    /* 0x58 'X' */ {0x63, 0x14, 0x08, 0x14, 0x63},
    /* 0x59 'Y' */ {0x03, 0x04, 0x78, 0x04, 0x03},
    /* 0x5A 'Z' */ {0x61, 0x51, 0x49, 0x45, 0x43},
    /* 0x5B '[' */ {0x00, 0x00, 0x7F, 0x41, 0x41},
    /* 0x5C '\' */ {0x02, 0x04, 0x08, 0x10, 0x20},
    /* 0x5D ']' */ {0x41, 0x41, 0x7F, 0x00, 0x00},
    /* 0x5E '^' */ {0x04, 0x02, 0x01, 0x02, 0x04},
    /* 0x5F '_' */ {0x40, 0x40, 0x40, 0x40, 0x40},
    /* 0x60 '`' */ {0x00, 0x01, 0x02, 0x04, 0x00},
    /* 0x61 'a' */ {0x20, 0x54, 0x54, 0x54, 0x78},
    /* 0x62 'b' */ {0x7F, 0x48, 0x44, 0x44, 0x38},
    /* 0x63 'c' */ {0x38, 0x44, 0x44, 0x44, 0x20},
    /* 0x64 'd' */ {0x38, 0x44, 0x44, 0x48, 0x7F},
    /* 0x65 'e' */ {0x38, 0x54, 0x54, 0x54, 0x18},
    /* 0x66 'f' */ {0x08, 0x7E, 0x09, 0x01, 0x02},
    /* 0x67 'g' */ {0x08, 0x14, 0x54, 0x54, 0x3C},
    /* 0x68 'h' */ {0x7F, 0x08, 0x04, 0x04, 0x78},
    /* 0x69 'i' */ {0x00, 0x44, 0x7D, 0x40, 0x00},
    /* 0x6A 'j' */ {0x20, 0x40, 0x44, 0x3D, 0x00},
    /* 0x6B 'k' */ {0x00, 0x7F, 0x10, 0x28, 0x44},
    /* 0x6C 'l' */ {0x00, 0x41, 0x7F, 0x40, 0x00},
    /* 0x6D 'm' */ {0x7C, 0x04, 0x18, 0x04, 0x78},
    /* 0x6E 'n' */ {0x7C, 0x08, 0x04, 0x04, 0x78},
    /* 0x6F 'o' */ {0x38, 0x44, 0x44, 0x44, 0x38},
    /* 0x70 'p' */ {0x7C, 0x14, 0x14, 0x14, 0x08},
    /* 0x71 'q' */ {0x08, 0x14, 0x14, 0x18, 0x7C},
    /* 0x72 'r' */ {0x7C, 0x08, 0x04, 0x04, 0x08},
    /* 0x73 's' */ {0x48, 0x54, 0x54, 0x54, 0x20},
    /* 0x74 't' */ {0x04, 0x3F, 0x44, 0x40, 0x20},
    /* 0x75 'u' */ {0x3C, 0x40, 0x40, 0x20, 0x7C},
    /* 0x76 'v' */ {0x1C, 0x20, 0x40, 0x20, 0x1C},
    /* 0x77 'w' */ {0x3C, 0x40, 0x30, 0x40, 0x3C},
    /* 0x78 'x' */ {0x44, 0x28, 0x10, 0x28, 0x44},
    /* 0x79 'y' */ {0x0C, 0x50, 0x50, 0x50, 0x3C},
    /* 0x7A 'z' */ {0x44, 0x64, 0x54, 0x4C, 0x44},
    /* 0x7B '{' */ {0x00, 0x08, 0x36, 0x41, 0x00},
    /* 0x7C '|' */ {0x00, 0x00, 0x7F, 0x00, 0x00},
    /* 0x7D '}' */ {0x00, 0x41, 0x36, 0x08, 0x00},
    /* 0x7E '~' */ {0x08, 0x04, 0x08, 0x10, 0x08},
};

/* ---------------------------------------------------------------------------
 * Public functions
 * ------------------------------------------------------------------------- */

int ch1116_init(ch1116_t *dev)
{
    int ret;

    if (dev == NULL) {
        return -1;
    }
    if (dev->i2c_write == NULL) {
        return -2;
    }

    /* Command sequence (CH1116 / SSD1306 compatible) */
    ret  = ch1116_send_command(dev, 0xAE);             /* Display off */
    ret |= ch1116_send_command(dev, 0xD5);             /* Set display clock divide ratio */
    ret |= ch1116_send_command(dev, 0x80);
    ret |= ch1116_send_command(dev, 0xA8);             /* Set multiplex ratio (64 rows) */
    ret |= ch1116_send_command(dev, 0x3F);
    ret |= ch1116_send_command(dev, 0xD3);             /* Set display offset */
    ret |= ch1116_send_command(dev, 0x00);
    ret |= ch1116_send_command(dev, 0x40);             /* Set start line (0) */
    ret |= ch1116_send_command(dev, 0xA1);             /* Segment remap (column 127 = SEG0) */
    ret |= ch1116_send_command(dev, 0xC8);             /* COM scan direction (remapped) */
    ret |= ch1116_send_command(dev, 0xDA);             /* COM pins hardware config */
    ret |= ch1116_send_command(dev, 0x12);
    ret |= ch1116_send_command(dev, 0x81);             /* Set contrast */
    ret |= ch1116_send_command(dev, 0x7F);
    ret |= ch1116_send_command(dev, 0xA4);             /* Resume to RAM content */
    ret |= ch1116_send_command(dev, 0xA6);             /* Normal display (not inverted) */
    ret |= ch1116_send_command(dev, 0x20);             /* Set memory addressing mode */
    ret |= ch1116_send_command(dev, 0x00);             /* Horizontal addressing mode */
    ret |= ch1116_send_command(dev, 0xAF);             /* Display on */

    return ret;
}

int ch1116_set_contrast(ch1116_t *dev, uint8_t contrast)
{
    int ret;

    if (dev == NULL) {
        return -1;
    }

    ret  = ch1116_send_command(dev, 0x81);
    ret |= ch1116_send_command(dev, contrast);

    return ret;
}

int ch1116_clear(ch1116_t *dev)
{
    int ret;
    uint8_t col, page;

    if (dev == NULL) {
        return -1;
    }

    /*
     * For page addressing mode: set column and page, then send 128 data bytes.
     * Repeat for all 8 pages.
     */
    for (page = 0; page < 8; page++) {
        ret  = ch1116_send_command(dev, 0xB0 | page);      /* Set page start */
        ret |= ch1116_send_command(dev, 0x00);              /* Set lower column = 0 */
        ret |= ch1116_send_command(dev, 0x10);              /* Set higher column = 0 */
        if (ret != 0) {
            return ret;
        }
        /* Write 128 zero data bytes */
        uint8_t zeros[128];
        for (col = 0; col < 128; col++) {
            zeros[col] = 0x00;
        }
        ret = ch1116_send_data_block(dev, zeros, 128);
        if (ret != 0) {
            return ret;
        }
    }

    return 0;
}

int ch1116_set_cursor(ch1116_t *dev, uint8_t col, uint8_t page)
{
    int ret;

    if (dev == NULL) {
        return -1;
    }

    if (page > 7) {
        page = 7;
    }
    if (col > 131) {
        col = 131;
    }

    ret  = ch1116_send_command(dev, 0xB0 | page);          /* Page start */
    ret |= ch1116_send_command(dev, 0x00 | (col & 0x0F)); /* Lower column */
    ret |= ch1116_send_command(dev, 0x10 | ((col >> 4) & 0x0F)); /* Higher column */

    return ret;
}

int ch1116_write_char(ch1116_t *dev, char c)
{
    uint8_t idx;
    uint8_t col;

    if (dev == NULL) {
        return -1;
    }

    /* Only printable ASCII */
    if (c < 0x20 || c > 0x7E) {
        return 0; /* silently skip */
    }

    idx = c - 0x20;

    /* Write 5 font columns */
    for (col = 0; col < 5; col++) {
        int ret = ch1116_send_data_byte(dev, font_5x7[idx][col]);
        if (ret != 0) {
            return ret;
        }
    }

    /* Write 1 blank column (spacing) */
    return ch1116_send_data_byte(dev, 0x00);
}

int ch1116_write_string(ch1116_t *dev, const char *str)
{
    int ret;

    if (dev == NULL || str == NULL) {
        return -1;
    }

    while (*str != '\0') {
        ret = ch1116_write_char(dev, *str);
        if (ret != 0) {
            return ret;
        }
        str++;
    }

    return 0;
}

int ch1116_draw_pixel(ch1116_t *dev, uint8_t x, uint8_t y, uint8_t pixel)
{
    uint8_t page;
    uint8_t bit_mask;
    int     ret;

    if (dev == NULL) {
        return -1;
    }

    if (x > 131 || y > 63) {
        return 0; /* out of bounds – silently ignore */
    }

    page     = y / 8;
    bit_mask = (uint8_t)(1u << (y % 8));

    /* Set cursor to the pixel's column/page */
    ret = ch1116_set_cursor(dev, x, page);
    if (ret != 0) {
        return ret;
    }

    /*
     * In page addressing mode, reading back the current byte is not easily done.
     * To keep the driver simple and avoid I²C read, we assume the display
     * has no memory or we simply overwrite the byte with a single pixel set/clear.
     * A proper implementation would require a shadow buffer, but for many
     * applications writing a pixel is used sparingly.
     *
     * Here we implement a blind write: we send the byte representing a single
     * pixel. This will clear the other 7 bits in that byte – which is acceptable
     * if the caller clears the area first or uses write_char. For more advanced
     * graphics a frame buffer is recommended.
     */
    uint8_t data = (pixel != 0) ? bit_mask : 0x00;
    return ch1116_send_data_byte(dev, data);
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
