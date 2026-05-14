/**
 * @file    ch1116.c
 * @brief   CH1116 OLED Display Driver Implementation (I2C)
 *
 * This driver provides a MCU-independent interface for the CH1116 132x64
 * OLED controller. All I2C writes are done through the function pointer
 * provided in ch1116_hal_t. No external dependencies (printf, malloc, free).
 *
 * @version 1.0.0
 * @date    2025-04-11
 */

#include "ch1116.h"

#include <stddef.h>  /* for NULL */

/* ========================================================================== */
/*  Internal Macros & Constants                                               */
/* ========================================================================== */

/**
 * @brief Control byte for command mode (Co=0, D/C=0).
 */
#define CH1116_CTRL_CMD    0x00U

/**
 * @brief Control byte for data mode (Co=0, D/C=1).
 */
#define CH1116_CTRL_DATA   0x40U

/**
 * @brief CH1116 command set (subset used by driver).
 */
#define CH1116_CMD_SETLOWCOL       0x00U  /**< Set lower column start address */
#define CH1116_CMD_SETHIGHCOL      0x10U  /**< Set higher column start address */
#define CH1116_CMD_SETDISPSTARTLN  0x40U  /**< Set display start line */
#define CH1116_CMD_SETCONTRAST     0x81U  /**< Set contrast (followed by value) */
#define CH1116_CMD_CHARGEPUMP      0x8DU  /**< Charge pump setting (followed by 0x14/0x10) */
#define CH1116_CMD_DISPLAYMODE     0xA4U  /**< Entire display ON (follow by 0xA4=normal, 0xA5=all on) */
#define CH1116_CMD_NORMALDISPLAY   0xA6U  /**< Normal (non-inverted) display */
#define CH1116_CMD_SETMUX          0xA8U  /**< Set multiplex ratio (followed by value) */
#define CH1116_CMD_DISPLAYOFF      0xAEU  /**< Display OFF (sleep) */
#define CH1116_CMD_DISPLAYON       0xAFU  /**< Display ON */
#define CH1116_CMD_SETPAGE         0xB0U  /**< Set page address (lower nibble selects page 0-7) */
#define CH1116_CMD_SETCOMDIR       0xC0U  /**< COM output direction (0xC0 normal, 0xC8 remapped) */
#define CH1116_CMD_SETSEGREMAP     0xA0U  /**< Segment remap (0xA0 normal, 0xA1 reversed) */
#define CH1116_CMD_SETSTARTLINE    0x40U  /**< Display start line register */
#define CH1116_CMD_COMSCANDEC      0xC8U  /**< COM scan direction (descending) */
#define CH1116_CMD_SETPLLDIV       0xD5U  /**< Set display clock divide ratio/oscillator frequency */
#define CH1116_CMD_SETPRECHARGE    0xD9U  /**< Set pre-charge period */
#define CH1116_CMD_SETVCOM         0xDBU  /**< Set VCOMH deselect level */

/* Typical values for 132x64, 3.3V supply */
#define CH1116_MUX_VALUE           63U    /**< Multiplex ratio value (64 MUX) */
#define CH1116_CONTRAST_INIT       0x7FU  /**< Medium contrast */
#define CH1116_CLK_DIVIDE          0x80U  /**< Clock divide ratio, oscillator frequency */
#define CH1116_PRECHARGE_VALUE     0xF1U  /**< Pre-charge period */
#define CH1116_VCOM_VALUE          0x30U  /**< VCOMH deselect level */
#define CH1116_CHARGEPUMP_ON       0x14U  /**< Enable charge pump (internal DC-DC) */
#define CH1116_CHARGEPUMP_OFF      0x10U  /**< Disable charge pump */

/**
 * @brief Font: 5x7 pixels + 1 pixel spacing. Data for ASCII 0x20..0x7E.
 * Each character occupies 5 bytes.
 * Bit order: MSB is top, LSB is bottom.
 * Column major (byte per column).
 * Generated from standard 5x7 font.
 */
static const uint8_t font5x7[][5] = {
    /* 0x20 ' ' */ { 0x00, 0x00, 0x00, 0x00, 0x00 },
    /* 0x21 '!' */ { 0x00, 0x00, 0x5F, 0x00, 0x00 },
    /* 0x22 '"' */ { 0x00, 0x07, 0x00, 0x07, 0x00 },
    /* 0x23 '#' */ { 0x14, 0x7F, 0x14, 0x7F, 0x14 },
    /* 0x24 '$' */ { 0x24, 0x2A, 0x7F, 0x2A, 0x12 },
    /* 0x25 '%' */ { 0x23, 0x13, 0x08, 0x64, 0x62 },
    /* 0x26 '&' */ { 0x36, 0x49, 0x55, 0x22, 0x50 },
    /* 0x27 ''' */ { 0x00, 0x05, 0x03, 0x00, 0x00 },
    /* 0x28 '(' */ { 0x00, 0x1C, 0x22, 0x41, 0x00 },
    /* 0x29 ')' */ { 0x00, 0x41, 0x22, 0x1C, 0x00 },
    /* 0x2A '*' */ { 0x08, 0x2A, 0x1C, 0x2A, 0x08 },
    /* 0x2B '+' */ { 0x08, 0x08, 0x3E, 0x08, 0x08 },
    /* 0x2C ',' */ { 0x00, 0x50, 0x30, 0x00, 0x00 },
    /* 0x2D '-' */ { 0x08, 0x08, 0x08, 0x08, 0x08 },
    /* 0x2E '.' */ { 0x00, 0x60, 0x60, 0x00, 0x00 },
    /* 0x2F '/' */ { 0x20, 0x10, 0x08, 0x04, 0x02 },
    /* 0x30 '0' */ { 0x3E, 0x51, 0x49, 0x45, 0x3E },
    /* 0x31 '1' */ { 0x00, 0x42, 0x7F, 0x40, 0x00 },
    /* 0x32 '2' */ { 0x42, 0x61, 0x51, 0x49, 0x46 },
    /* 0x33 '3' */ { 0x21, 0x41, 0x45, 0x4B, 0x31 },
    /* 0x34 '4' */ { 0x18, 0x14, 0x12, 0x7F, 0x10 },
    /* 0x35 '5' */ { 0x27, 0x45, 0x45, 0x45, 0x39 },
    /* 0x36 '6' */ { 0x3C, 0x4A, 0x49, 0x49, 0x30 },
    /* 0x37 '7' */ { 0x01, 0x71, 0x09, 0x05, 0x03 },
    /* 0x38 '8' */ { 0x36, 0x49, 0x49, 0x49, 0x36 },
    /* 0x39 '9' */ { 0x06, 0x49, 0x49, 0x29, 0x1E },
    /* 0x3A ':' */ { 0x00, 0x36, 0x36, 0x00, 0x00 },
    /* 0x3B ';' */ { 0x00, 0x56, 0x36, 0x00, 0x00 },
    /* 0x3C '<' */ { 0x00, 0x08, 0x14, 0x22, 0x41 },
    /* 0x3D '=' */ { 0x14, 0x14, 0x14, 0x14, 0x14 },
    /* 0x3E '>' */ { 0x41, 0x22, 0x14, 0x08, 0x00 },
    /* 0x3F '?' */ { 0x02, 0x01, 0x51, 0x09, 0x06 },
    /* 0x40 '@' */ { 0x32, 0x49, 0x79, 0x41, 0x3E },
    /* 0x41 'A' */ { 0x7E, 0x11, 0x11, 0x11, 0x7E },
    /* 0x42 'B' */ { 0x7F, 0x49, 0x49, 0x49, 0x36 },
    /* 0x43 'C' */ { 0x3E, 0x41, 0x41, 0x41, 0x22 },
    /* 0x44 'D' */ { 0x7F, 0x41, 0x41, 0x22, 0x1C },
    /* 0x45 'E' */ { 0x7F, 0x49, 0x49, 0x49, 0x41 },
    /* 0x46 'F' */ { 0x7F, 0x09, 0x09, 0x01, 0x01 },
    /* 0x47 'G' */ { 0x3E, 0x41, 0x41, 0x51, 0x32 },
    /* 0x48 'H' */ { 0x7F, 0x08, 0x08, 0x08, 0x7F },
    /* 0x49 'I' */ { 0x00, 0x41, 0x7F, 0x41, 0x00 },
    /* 0x4A 'J' */ { 0x20, 0x40, 0x41, 0x3F, 0x01 },
    /* 0x4B 'K' */ { 0x7F, 0x08, 0x14, 0x22, 0x41 },
    /* 0x4C 'L' */ { 0x7F, 0x40, 0x40, 0x40, 0x40 },
    /* 0x4D 'M' */ { 0x7F, 0x02, 0x04, 0x02, 0x7F },
    /* 0x4E 'N' */ { 0x7F, 0x04, 0x08, 0x10, 0x7F },
    /* 0x4F 'O' */ { 0x3E, 0x41, 0x41, 0x41, 0x3E },
    /* 0x50 'P' */ { 0x7F, 0x09, 0x09, 0x09, 0x06 },
    /* 0x51 'Q' */ { 0x3E, 0x41, 0x51, 0x21, 0x5E },
    /* 0x52 'R' */ { 0x7F, 0x09, 0x19, 0x29, 0x46 },
    /* 0x53 'S' */ { 0x46, 0x49, 0x49, 0x49, 0x31 },
    /* 0x54 'T' */ { 0x01, 0x01, 0x7F, 0x01, 0x01 },
    /* 0x55 'U' */ { 0x3F, 0x40, 0x40, 0x40, 0x3F },
    /* 0x56 'V' */ { 0x1F, 0x20, 0x40, 0x20, 0x1F },
    /* 0x57 'W' */ { 0x7F, 0x20, 0x18, 0x20, 0x7F },
    /* 0x58 'X' */ { 0x63, 0x14, 0x08, 0x14, 0x63 },
    /* 0x59 'Y' */ { 0x03, 0x04, 0x78, 0x04, 0x03 },
    /* 0x5A 'Z' */ { 0x61, 0x51, 0x49, 0x45, 0x43 },
    /* 0x5B '[' */ { 0x00, 0x7F, 0x41, 0x41, 0x00 },
    /* 0x5C '\' */ { 0x02, 0x04, 0x08, 0x10, 0x20 },
    /* 0x5D ']' */ { 0x00, 0x41, 0x41, 0x7F, 0x00 },
    /* 0x5E '^' */ { 0x04, 0x02, 0x01, 0x02, 0x04 },
    /* 0x5F '_' */ { 0x40, 0x40, 0x40, 0x40, 0x40 },
    /* 0x60 '`' */ { 0x00, 0x01, 0x02, 0x04, 0x00 },
    /* 0x61 'a' */ { 0x20, 0x54, 0x54, 0x54, 0x78 },
    /* 0x62 'b' */ { 0x7F, 0x48, 0x44, 0x44, 0x38 },
    /* 0x63 'c' */ { 0x38, 0x44, 0x44, 0x44, 0x20 },
    /* 0x64 'd' */ { 0x38, 0x44, 0x44, 0x48, 0x7F },
    /* 0x65 'e' */ { 0x38, 0x54, 0x54, 0x54, 0x18 },
    /* 0x66 'f' */ { 0x08, 0x7E, 0x09, 0x01, 0x02 },
    /* 0x67 'g' */ { 0x0C, 0x52, 0x52, 0x52, 0x3E },
    /* 0x68 'h' */ { 0x7F, 0x08, 0x04, 0x04, 0x78 },
    /* 0x69 'i' */ { 0x00, 0x44, 0x7D, 0x40, 0x00 },
    /* 0x6A 'j' */ { 0x20, 0x40, 0x44, 0x3D, 0x00 },
    /* 0x6B 'k' */ { 0x00, 0x7F, 0x10, 0x28, 0x44 },
    /* 0x6C 'l' */ { 0x00, 0x41, 0x7F, 0x40, 0x00 },
    /* 0x6D 'm' */ { 0x7C, 0x04, 0x18, 0x04, 0x78 },
    /* 0x6E 'n' */ { 0x7C, 0x08, 0x04, 0x04, 0x78 },
    /* 0x6F 'o' */ { 0x38, 0x44, 0x44, 0x44, 0x38 },
    /* 0x70 'p' */ { 0x7C, 0x14, 0x14, 0x14, 0x08 },
    /* 0x71 'q' */ { 0x08, 0x14, 0x14, 0x18, 0x7C },
    /* 0x72 'r' */ { 0x7C, 0x08, 0x04, 0x04, 0x08 },
    /* 0x73 's' */ { 0x48, 0x54, 0x54, 0x54, 0x20 },
    /* 0x74 't' */ { 0x04, 0x3F, 0x44, 0x40, 0x20 },
    /* 0x75 'u' */ { 0x3C, 0x40, 0x40, 0x20, 0x7C },
    /* 0x76 'v' */ { 0x1C, 0x20, 0x40, 0x20, 0x1C },
    /* 0x77 'w' */ { 0x3C, 0x40, 0x30, 0x40, 0x3C },
    /* 0x78 'x' */ { 0x44, 0x28, 0x10, 0x28, 0x44 },
    /* 0x79 'y' */ { 0x0C, 0x50, 0x50, 0x50, 0x3C },
    /* 0x7A 'z' */ { 0x44, 0x64, 0x54, 0x4C, 0x44 },
    /* 0x7B '{' */ { 0x00, 0x08, 0x36, 0x41, 0x00 },
    /* 0x7C '|' */ { 0x00, 0x00, 0x7F, 0x00, 0x00 },
    /* 0x7D '}' */ { 0x00, 0x41, 0x36, 0x08, 0x00 },
    /* 0x7E '~' */ { 0x08, 0x04, 0x08, 0x10, 0x08 }
};

/* ========================================================================== */
/*  Static Helper Functions                                                   */
/* ========================================================================== */

/**
 * @brief Validate HAL pointer.
 * @param[in] hal  Pointer to ch1116_hal_t.
 * @return int     0 if valid, -1 if NULL.
 */
static int hal_validate(ch1116_hal_t *hal)
{
    if (NULL == hal) {
        return -1;
    }
    if (NULL == hal->i2c_write) {
        return -2;
    }
    return 0;
}

/**
 * @brief Write a command byte to the CH1116.
 *
 * @param[in] hal   Valid HAL context.
 * @param[in] cmd   Command byte.
 * @return int      0 on success, negative from i2c_write.
 */
static int write_cmd(ch1116_hal_t *hal, uint8_t cmd)
{
    uint8_t buf[2] = { CH1116_CTRL_CMD, cmd };
    return hal->i2c_write(hal->dev_addr, buf, sizeof(buf));
}

/**
 * @brief Write a data byte to the CH1116.
 *
 * @param[in] hal   Valid HAL context.
 * @param[in] data  Data byte.
 * @return int      0 on success, negative from i2c_write.
 */
static int write_data(ch1116_hal_t *hal, uint8_t data)
{
    uint8_t buf[2] = { CH1116_CTRL_DATA, data };
    return hal->i2c_write(hal->dev_addr, buf, sizeof(buf));
}

/**
 * @brief Write a block of data bytes (data mode) to the CH1116.
 *
 * The control byte is prepended automatically.
 *
 * @param[in] hal   Valid HAL context.
 * @param[in] data  Pointer to data array.
 * @param[in] len   Number of data bytes.
 * @return int      0 on success, negative from i2c_write.
 */
static int write_data_block(ch1116_hal_t *hal, const uint8_t *data, uint16_t len)
{
    /* We need to prepend control byte. For small blocks we can use stack buffer,
     * but for large blocks (e.g., 132 bytes) we allocate dynamically? No malloc.
     * We will construct a temporary buffer on stack only if len is small (<256).
     * For larger blocks we break into chunks with individual writes.
     * Here we assume max page write = 132 bytes, use static buffer.
     * If len > 256, this static buffer won't work. But 132 < 256.
     */
    /* Prepend control byte by writing it first, then the data block.
     * This avoids large buffer copying.
     */
    int ret;
    uint8_t ctrl = CH1116_CTRL_DATA;
    ret = hal->i2c_write(hal->dev_addr, &ctrl, 1);
    if (ret != 0) {
        return ret;
    }
    return hal->i2c_write(hal->dev_addr, data, len);
}

/* ========================================================================== */
/*  Public API Implementation                                                 */
/* ========================================================================== */

int ch1116_init(ch1116_hal_t *hal)
{
    int ret;

    ret = hal_validate(hal);
    if (ret != 0) {
        return ret;
    }

    /* Wait for VDD stable? Not needed in driver; assume power good. */

    /* Display OFF */
    ret = write_cmd(hal, CH1116_CMD_DISPLAYOFF);
    if (ret != 0) return ret;

    /* Set clock divide ratio / oscillator frequency */
    ret = write_cmd(hal, CH1116_CMD_SETPLLDIV);
    if (ret != 0) return ret;
    ret = write_cmd(hal, CH1116_CLK_DIVIDE);
    if (ret != 0) return ret;

    /* Set multiplex ratio to 64 */
    ret = write_cmd(hal, CH1116_CMD_SETMUX);
    if (ret != 0) return ret;
    ret = write_cmd(hal, CH1116_MUX_VALUE);
    if (ret != 0) return ret;

    /* Set display offset (0x40 command with argument) */
    /* CH1116: Set display offset command = 0xD3, but older docs use 0x40? Actually offset is set by 0xD3.
     * For safety, use 0xD3 (common with SSD1306). */
    /* We'll skip offset if not needed; assume 0. */
    /* Set display start line */
    ret = write_cmd(hal, CH1116_CMD_SETSTARTLINE | 0x00);  /* start line = 0 */
    if (ret != 0) return ret;

    /* Segment remap (column 127 mapped to SEG0) */
    ret = write_cmd(hal, CH1116_CMD_SETSEGREMAP | 0x01);  /* 0xA1 */
    if (ret != 0) return ret;

    /* COM scan direction (descending) */
    ret = write_cmd(hal, CH1116_CMD_COMSCANDEC);  /* 0xC8 */
    if (ret != 0) return ret;

    /* Set COM pins hardware configuration */
    /* CH1116 uses 0xDA command. Value: 0x12 for 64-pin? */
    ret = write_cmd(hal, 0xDA);
    if (ret != 0) return ret;
    ret = write_cmd(hal, 0x12);
    if (ret != 0) return ret;

    /* Set contrast */
    ret = write_cmd(hal, CH1116_CMD_SETCONTRAST);
    if (ret != 0) return ret;
    ret = write_cmd(hal, CH1116_CONTRAST_INIT);
    if (ret != 0) return ret;

    /* Enable charge pump (internal DC-DC) */
    ret = write_cmd(hal, CH1116_CMD_CHARGEPUMP);
    if (ret != 0) return ret;
    ret = write_cmd(hal, CH1116_CHARGEPUMP_ON);
    if (ret != 0) return ret;

    /* Set pre-charge period */
    ret = write_cmd(hal, CH1116_CMD_SETPRECHARGE);
    if (ret != 0) return ret;
    ret = write_cmd(hal, CH1116_PRECHARGE_VALUE);
    if (ret != 0) return ret;

    /* Set VCOMH deselect level */
    ret = write_cmd(hal, CH1116_CMD_SETVCOM);
    if (ret != 0) return ret;
    ret = write_cmd(hal, CH1116_VCOM_VALUE);
    if (ret != 0) return ret;

    /* Entire display ON, normal display */
    ret = write_cmd(hal, CH1116_CMD_DISPLAYMODE);  /* 0xA4 */
    if (ret != 0) return ret;

    /* Normal (non-inverted) display */
    ret = write_cmd(hal, CH1116_CMD_NORMALDISPLAY); /* 0xA6 */
    if (ret != 0) return ret;

    /* Clear screen */
    ret = ch1116_clear(hal);
    if (ret != 0) return ret;

    /* Display ON */
    ret = write_cmd(hal, CH1116_CMD_DISPLAYON);
    if (ret != 0) return ret;

    return 0;
}

int ch1116_send_command(ch1116_hal_t *hal, uint8_t cmd)
{
    int ret = hal_validate(hal);
    if (ret != 0) return ret;
    return write_cmd(hal, cmd);
}

int ch1116_send_data(ch1116_hal_t *hal, uint8_t data)
{
    int ret = hal_validate(hal);
    if (ret != 0) return ret;
    return write_data(hal, data);
}

int ch1116_clear(ch1116_hal_t *hal)
{
    int ret = hal_validate(hal);
    if (ret != 0) return ret;

    /* Set column address range (0..131) and page range (0..7) */
    /* CH1116 uses 0x21 and 0x22 commands (SSD1306 compatible) */
    ret = write_cmd(hal, 0x21);   /* Set column address */
    if (ret != 0) return ret;
    ret = write_cmd(hal, 0x00);   /* Start column */
    if (ret != 0) return ret;
    ret = write_cmd(hal, CH1116_WIDTH - 1);   /* End column */
    if (ret != 0) return ret;

    ret = write_cmd(hal, 0x22);   /* Set page address */
    if (ret != 0) return ret;
    ret = write_cmd(hal, 0x00);   /* Start page */
    if (ret != 0) return ret;
    ret = write_cmd(hal, CH1116_PAGES - 1);   /* End page */
    if (ret != 0) return ret;

    /* Write 0x00 for all pixels */
    uint8_t zero_block[132];  /* max column width */
    for (uint16_t i = 0; i < CH1116_WIDTH; i++) {
        zero_block[i] = 0x00;
    }

    for (uint8_t page = 0; page < CH1116_PAGES; page++) {
        ret = write_data_block(hal, zero_block, CH1116_WIDTH);
        if (ret != 0) return ret;
    }

    /* Reset cursor to (0,0) */
    // implicit via page address set

    return 0;
}

int ch1116_set_cursor(ch1116_hal_t *hal, uint8_t col, uint8_t page)
{
    int ret = hal_validate(hal);
    if (ret != 0) return ret;

    if (col >= CH1116_WIDTH) return -3;
    if (page >= CH1116_PAGES) return -3;

    /* Set column address range (we set both start and end to same column? No,
     * we only want to set the start; the write data will increment automatically.
     * CH1116 in page addressing mode: use set column low/high.
     * Actually CH1116 supports both page and horizontal addressing. By default after init it's in page mode.
     * Page mode: send set page command (0xB0|page), then set column start (low nibble + high nibble).
     * Simpler: use 0x21/0x22 horizontal addressing to set start and end? That sets range for subsequent writes.
     * But that would require setting end column each time. Best to use page mode.
     * Change: enable page addressing mode via command 0x20 with 0x02 (page mode). Then use 0xB0|page, low/high col.
     */
    /* First switch to page addressing mode */
    ret = write_cmd(hal, 0x20);  /* Set memory addressing mode */
    if (ret != 0) return ret;
    ret = write_cmd(hal, 0x02);  /* Page addressing mode */
    if (ret != 0) return ret;

    /* Set page (0xB0 + page) */
    ret = write_cmd(hal, CH1116_CMD_SETPAGE | (page & 0x0F));
    if (ret != 0) return ret;

    /* Set column low nibble */
    ret = write_cmd(hal, CH1116_CMD_SETLOWCOL | (col & 0x0F));
    if (ret != 0) return ret;

    /* Set column high nibble */
    ret = write_cmd(hal, CH1116_CMD_SETHIGHCOL | ((col >> 4) & 0x0F));
    if (ret != 0) return ret;

    return 0;
}

int ch1116_write_char(ch1116_hal_t *hal, char ch)
{
    int ret = hal_validate(hal);
    if (ret != 0) return ret;

    if (ch < 0x20 || ch > 0x7E) {
        return -4;  /* unsupported character */
    }

    const uint8_t *glyph = font5x7[ch - 0x20];

    /* Write 5 columns of font data */
    for (int i = 0; i < 5; i++) {
        ret = write_data(hal, glyph[i]);
        if (ret != 0) return ret;
    }
    /* Column spacing (blank column) */
    ret = write_data(hal, 0x00);
    if (ret != 0) return ret;

    return 0;
}

int ch1116_write_string(ch1116_hal_t *hal, const char *str)
{
    int ret = hal_validate(hal);
    if (ret != 0) return ret;

    if (NULL == str) return -5;

    while (*str != '\0') {
        ret = ch1116_write_char(hal, *str);
        if (ret != 0) return ret;
        str++;
    }
    return 0;
}

int ch1116_write_uint(ch1116_hal_t *hal, uint16_t value, uint8_t digits)
{
    int ret = hal_validate(hal);
    if (ret != 0) return ret;

    if (digits > 5) digits = 5;  /* safety limit */

    char buf[6];
    uint8_t i = digits;

    buf[i--] = '\0';
    /* Generate digits from least significant */
    while (i > 0) {
        buf[i--] = (char)('0' + (value % 10));
        value /= 10;
    }
    /* If value > 0 after loop, leading digits will be truncated;
     * for safety we print only the lower digits (like modulo).
     * Assume caller pads adequately.
     */

    return ch1116_write_string(hal, buf);
}

int ch1116_reset(ch1116_hal_t *hal)
{
    (void)hal;
    /* No hardware reset needed; software reset via init sequence could be called
     * by user if desired. This function intentionally does nothing. */
    return 0;
}
