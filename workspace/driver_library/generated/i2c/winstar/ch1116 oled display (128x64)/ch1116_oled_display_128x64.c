/*
 * CH1116 OLED I2C Driver (MCU-agnostic)
 *
 * Provides initialization, command/data transmission, cursor control,
 * display clearing, string output with a built-in 5x7 font, and software reset.
 *
 * All I2C operations are performed through a user-supplied HAL interface,
 * making the driver independent of any specific MCU or HAL library.
 */

#include "ch1116_i2c_driver.h"
#include <stddef.h>  /* for NULL */

/* ---------------------------------------------------------------------------
 * Internal constants
 * -------------------------------------------------------------------------*/

/* I2C control bytes (see CH1116 datasheet) */
#define CH1116_CTRL_CMD    0x00U   /* Co=0, D/C#=0 => command */
#define CH1116_CTRL_DATA   0x40U   /* Co=0, D/C#=1 => data */

/* CH1116 basic commands */
#define CH1116_CMD_DISPLAY_OFF       0xAEU
#define CH1116_CMD_DISPLAY_ON        0xAFU
#define CH1116_CMD_SET_CONTRAST      0x81U
#define CH1116_CMD_SET_SEGMENT_REMAP 0xA0U
#define CH1116_CMD_SET_COM_SCAN_DIR  0xC0U
#define CH1116_CMD_SET_DISPLAY_START 0x40U
#define CH1116_CMD_SET_MUX_RATIO     0xA8U
#define CH1116_CMD_SET_DISPLAY_OFFSET 0xD3U
#define CH1116_CMD_SET_CLOCK_DIV     0xD5U
#define CH1116_CMD_SET_PRECHARGE     0xD9U
#define CH1116_CMD_SET_COM_PINS      0xDAU
#define CH1116_CMD_SET_VCOMH         0xDBU
#define CH1116_CMD_CHARGE_PUMP       0x8DU
#define CH1116_CMD_MEMORY_MODE       0x20U
#define CH1116_CMD_DEACTIVATE_SCROLL 0x2EU
#define CH1116_CMD_SOFT_RESET        0xE2U

/* Page addressing command base */
#define CH1116_CMD_SET_PAGE_BASE     0xB0U

/* Column address high/low nibble */
#define CH1116_CMD_SET_COL_LOW_BASE  0x00U
#define CH1116_CMD_SET_COL_HIGH_BASE 0x10U

/* Display dimensions */
#define CH1116_COLUMNS      132U
#define CH1116_PAGES        8U

/* Font: 5x7 pixel, 1 byte per column, 5 columns per character */
#define FONT_WIDTH          5U
#define FONT_FIRST_CHAR     0x20U   /* space */
#define FONT_LAST_CHAR      0x7EU   /* ~ */
#define FONT_CHAR_COUNT     (FONT_LAST_CHAR - FONT_FIRST_CHAR + 1U)

/* ---------------------------------------------------------------------------
 * I2C low-level helpers (private)
 * -------------------------------------------------------------------------*/

/**
 * @brief Write a single command byte via I2C.
 * @param oled  CH1116 instance.
 * @param cmd   Command byte.
 * @return 0 on success, negative on failure.
 */
static int i2c_write_command(ch1116_t *oled, uint8_t cmd)
{
    uint8_t buf[2];
    buf[0] = CH1116_CTRL_CMD;
    buf[1] = cmd;
    return oled->hal->i2c_write(oled->i2c_addr, buf, 2U);
}

/**
 * @brief Write multiple command bytes in one I2C transaction.
 * @param oled  CH1116 instance.
 * @param cmds  Pointer to command byte array.
 * @param len   Number of command bytes.
 * @return 0 on success, negative on failure.
 */
static int i2c_write_command_list(ch1116_t *oled, const uint8_t *cmds, uint16_t len)
{
    /* For simplicity, send each command with its own control byte.
       A more efficient implementation could combine multiple commands in one
       transaction if the controller supports it, but this general approach works. */
    for (uint16_t i = 0U; i < len; i++)
    {
        int ret = i2c_write_command(oled, cmds[i]);
        if (ret != 0)
        {
            return ret;
        }
    }
    return 0;
}

/**
 * @brief Write a single data byte via I2C.
 * @param oled  CH1116 instance.
 * @param data  Data byte.
 * @return 0 on success, negative on failure.
 */
static int i2c_write_data(ch1116_t *oled, uint8_t data)
{
    uint8_t buf[2];
    buf[0] = CH1116_CTRL_DATA;
    buf[1] = data;
    return oled->hal->i2c_write(oled->i2c_addr, buf, 2U);
}

/**
 * @brief Write a buffer of data bytes via I2C.
 * @param oled  CH1116 instance.
 * @param buf   Pointer to data buffer.
 * @param len   Number of data bytes.
 * @return 0 on success, negative on failure.
 */
static int i2c_write_data_buffer(ch1116_t *oled, const uint8_t *buf, uint16_t len)
{
    /* For simplicity, send each data byte with its own control byte.
       Using a single control byte + multiple data bytes would be more efficient
       but requires the controller to support auto-increment in data mode.
       To be safe, we keep it simple. */
    for (uint16_t i = 0U; i < len; i++)
    {
        int ret = i2c_write_data(oled, buf[i]);
        if (ret != 0)
        {
            return ret;
        }
    }
    return 0;
}

/* ---------------------------------------------------------------------------
 * 5x7 pixel font (ASCII 0x20..0x7E)
 * Generated from common dot matrix font.
 * -------------------------------------------------------------------------*/
static const uint8_t font_5x7[FONT_CHAR_COUNT][FONT_WIDTH] = {
    {0x00, 0x00, 0x00, 0x00, 0x00}, /* space */
    {0x00, 0x00, 0x5F, 0x00, 0x00}, /* ! */
    {0x00, 0x07, 0x00, 0x07, 0x00}, /* " */
    {0x14, 0x7F, 0x14, 0x7F, 0x14}, /* # */
    {0x24, 0x2A, 0x7F, 0x2A, 0x12}, /* $ */
    {0x23, 0x13, 0x08, 0x64, 0x62}, /* % */
    {0x36, 0x49, 0x55, 0x22, 0x50}, /* & */
    {0x00, 0x05, 0x03, 0x00, 0x00}, /* ' */
    {0x00, 0x1C, 0x22, 0x41, 0x00}, /* ( */
    {0x00, 0x41, 0x22, 0x1C, 0x00}, /* ) */
    {0x08, 0x2A, 0x1C, 0x2A, 0x08}, /* * */
    {0x08, 0x08, 0x3E, 0x08, 0x08}, /* + */
    {0x00, 0x50, 0x30, 0x00, 0x00}, /* , */
    {0x08, 0x08, 0x08, 0x08, 0x08}, /* - */
    {0x00, 0x60, 0x60, 0x00, 0x00}, /* . */
    {0x20, 0x10, 0x08, 0x04, 0x02}, /* / */
    {0x3E, 0x51, 0x49, 0x45, 0x3E}, /* 0 */
    {0x00, 0x42, 0x7F, 0x40, 0x00}, /* 1 */
    {0x42, 0x61, 0x51, 0x49, 0x46}, /* 2 */
    {0x21, 0x41, 0x45, 0x4B, 0x31}, /* 3 */
    {0x18, 0x14, 0x12, 0x7F, 0x10}, /* 4 */
    {0x27, 0x45, 0x45, 0x45, 0x39}, /* 5 */
    {0x3C, 0x4A, 0x49, 0x49, 0x30}, /* 6 */
    {0x01, 0x71, 0x09, 0x05, 0x03}, /* 7 */
    {0x36, 0x49, 0x49, 0x49, 0x36}, /* 8 */
    {0x06, 0x49, 0x49, 0x29, 0x1E}, /* 9 */
    {0x00, 0x36, 0x36, 0x00, 0x00}, /* : */
    {0x00, 0x56, 0x36, 0x00, 0x00}, /* ; */
    {0x00, 0x08, 0x14, 0x22, 0x41}, /* < */
    {0x14, 0x14, 0x14, 0x14, 0x14}, /* = */
    {0x41, 0x22, 0x14, 0x08, 0x00}, /* > */
    {0x02, 0x01, 0x51, 0x09, 0x06}, /* ? */
    {0x32, 0x49, 0x79, 0x41, 0x3E}, /* @ */
    {0x7E, 0x11, 0x11, 0x11, 0x7E}, /* A */
    {0x7F, 0x49, 0x49, 0x49, 0x36}, /* B */
    {0x3E, 0x41, 0x41, 0x41, 0x22}, /* C */
    {0x7F, 0x41, 0x41, 0x22, 0x1C}, /* D */
    {0x7F, 0x49, 0x49, 0x49, 0x41}, /* E */
    {0x7F, 0x09, 0x09, 0x01, 0x01}, /* F */
    {0x3E, 0x41, 0x41, 0x51, 0x32}, /* G */
    {0x7F, 0x08, 0x08, 0x08, 0x7F}, /* H */
    {0x00, 0x41, 0x7F, 0x41, 0x00}, /* I */
    {0x20, 0x40, 0x41, 0x3F, 0x01}, /* J */
    {0x7F, 0x08, 0x14, 0x22, 0x41}, /* K */
    {0x7F, 0x40, 0x40, 0x40, 0x40}, /* L */
    {0x7F, 0x02, 0x04, 0x02, 0x7F}, /* M */
    {0x7F, 0x04, 0x08, 0x10, 0x7F}, /* N */
    {0x3E, 0x41, 0x41, 0x41, 0x3E}, /* O */
    {0x7F, 0x09, 0x09, 0x09, 0x06}, /* P */
    {0x3E, 0x41, 0x51, 0x21, 0x5E}, /* Q */
    {0x7F, 0x09, 0x19, 0x29, 0x46}, /* R */
    {0x46, 0x49, 0x49, 0x49, 0x31}, /* S */
    {0x01, 0x01, 0x7F, 0x01, 0x01}, /* T */
    {0x3F, 0x40, 0x40, 0x40, 0x3F}, /* U */
    {0x1F, 0x20, 0x40, 0x20, 0x1F}, /* V */
    {0x7F, 0x20, 0x18, 0x20, 0x7F}, /* W */
    {0x63, 0x14, 0x08, 0x14, 0x63}, /* X */
    {0x03, 0x04, 0x78, 0x04, 0x03}, /* Y */
    {0x61, 0x51, 0x49, 0x45, 0x43}, /* Z */
    {0x00, 0x00, 0x7F, 0x41, 0x41}, /* [ */
    {0x02, 0x04, 0x08, 0x10, 0x20}, /* \ */
    {0x41, 0x41, 0x7F, 0x00, 0x00}, /* ] */
    {0x04, 0x02, 0x01, 0x02, 0x04}, /* ^ */
    {0x40, 0x40, 0x40, 0x40, 0x40}, /* _ */
    {0x00, 0x01, 0x02, 0x04, 0x00}, /* ` */
    {0x20, 0x54, 0x54, 0x54, 0x78}, /* a */
    {0x7F, 0x48, 0x44, 0x44, 0x38}, /* b */
    {0x38, 0x44, 0x44, 0x44, 0x20}, /* c */
    {0x38, 0x44, 0x44, 0x48, 0x7F}, /* d */
    {0x38, 0x54, 0x54, 0x54, 0x18}, /* e */
    {0x08, 0x7E, 0x09, 0x01, 0x02}, /* f */
    {0x08, 0x14, 0x54, 0x54, 0x3C}, /* g */
    {0x7F, 0x08, 0x04, 0x04, 0x78}, /* h */
    {0x00, 0x44, 0x7D, 0x40, 0x00}, /* i */
    {0x20, 0x40, 0x44, 0x3D, 0x00}, /* j */
    {0x00, 0x7F, 0x10, 0x28, 0x44}, /* k */
    {0x00, 0x41, 0x7F, 0x40, 0x00}, /* l */
    {0x7C, 0x04, 0x18, 0x04, 0x78}, /* m */
    {0x7C, 0x08, 0x04, 0x04, 0x78}, /* n */
    {0x38, 0x44, 0x44, 0x44, 0x38}, /* o */
    {0x7C, 0x14, 0x14, 0x14, 0x08}, /* p */
    {0x08, 0x14, 0x14, 0x18, 0x7C}, /* q */
    {0x7C, 0x08, 0x04, 0x04, 0x08}, /* r */
    {0x48, 0x54, 0x54, 0x54, 0x20}, /* s */
    {0x04, 0x3F, 0x44, 0x40, 0x20}, /* t */
    {0x3C, 0x40, 0x40, 0x20, 0x7C}, /* u */
    {0x1C, 0x20, 0x40, 0x20, 0x1C}, /* v */
    {0x3C, 0x40, 0x30, 0x40, 0x3C}, /* w */
    {0x44, 0x28, 0x10, 0x28, 0x44}, /* x */
    {0x0C, 0x50, 0x50, 0x50, 0x3C}, /* y */
    {0x44, 0x64, 0x54, 0x4C, 0x44}, /* z */
    {0x00, 0x08, 0x36, 0x41, 0x00}, /* { */
    {0x00, 0x00, 0x7F, 0x00, 0x00}, /* | */
    {0x00, 0x41, 0x36, 0x08, 0x00}, /* } */
    {0x08, 0x08, 0x2A, 0x1C, 0x08}, /* ~ */
};

/* ---------------------------------------------------------------------------
 * Public API implementation
 * -------------------------------------------------------------------------*/

int ch1116_init(ch1116_t *oled, ch1116_hal_t *hal, uint8_t i2c_addr)
{
    int ret;

    /* Parameter validation */
    if ((oled == NULL) || (hal == NULL) || (hal->i2c_write == NULL))
    {
        return -1;
    }

    /* Attach HAL and address */
    oled->hal      = hal;
    oled->i2c_addr = i2c_addr;
    oled->current_col   = 0U;
    oled->current_page  = 0U;

    /* Optional software reset before init */
    ret = ch1116_software_reset(oled);
    if (ret != 0)
    {
        return ret;
    }

    /* Initialization sequence (typical for CH1116, adjust if needed) */
    const uint8_t init_cmds[] = {
        CH1116_CMD_DISPLAY_OFF,           /* 0xAE */
        CH1116_CMD_SET_CLOCK_DIV,         /* 0xD5 */
        0x80U,                            /* suggested clock div/osc freq */
        CH1116_CMD_SET_MUX_RATIO,         /* 0xA8 */
        0x3FU,                            /* multiplex ratio = 64 */
        CH1116_CMD_SET_DISPLAY_OFFSET,    /* 0xD3 */
        0x00U,                            /* no offset */
        CH1116_CMD_SET_DISPLAY_START,     /* 0x40 | 0x00 */
        CH1116_CMD_SET_SEGMENT_REMAP,     /* 0xA0 | 0x01 => remap (col 127->0) */
        0x01U,                            /* remap */
        CH1116_CMD_SET_COM_SCAN_DIR,      /* 0xC0 | 0x08 => scan from COM63 to COM0 */
        0x08U,                            /* COM scan direction reversed */
        CH1116_CMD_SET_COM_PINS,          /* 0xDA */
        0x12U,                            /* alternative pin configuration */
        CH1116_CMD_SET_CONTRAST,          /* 0x81 */
        0x7FU,                            /* contrast value */
        CH1116_CMD_SET_PRECHARGE,         /* 0xD9 */
        0xF1U,                            /* pre-charge period */
        CH1116_CMD_SET_VCOMH,             /* 0xDB */
        0x40U,                            /* VCOMH deselect level */
        CH1116_CMD_CHARGE_PUMP,           /* 0x8D */
        0x14U,                            /* enable charge pump */
        CH1116_CMD_MEMORY_MODE,           /* 0x20 */
        0x00U,                            /* horizontal addressing mode */
        CH1116_CMD_DEACTIVATE_SCROLL,     /* 0x2E */
        CH1116_CMD_DISPLAY_ON             /* 0xAF */
    };

    ret = i2c_write_command_list(oled, init_cmds, sizeof(init_cmds));
    if (ret != 0)
    {
        return ret;
    }

    /* Clear display after init */
    ret = ch1116_clear_display(oled);
    if (ret != 0)
    {
        return ret;
    }

    /* Set cursor to home (0,0) */
    ret = ch1116_set_cursor(oled, 0U, 0U);
    if (ret != 0)
    {
        return ret;
    }

    return 0;
}

int ch1116_send_command(ch1116_t *oled, uint8_t cmd)
{
    if ((oled == NULL) || (oled->hal == NULL) || (oled->hal->i2c_write == NULL))
    {
        return -1;
    }
    return i2c_write_command(oled, cmd);
}

int ch1116_send_data(ch1116_t *oled, uint8_t data)
{
    if ((oled == NULL) || (oled->hal == NULL) || (oled->hal->i2c_write == NULL))
    {
        return -1;
    }
    return i2c_write_data(oled, data);
}

int ch1116_set_cursor(ch1116_t *oled, uint8_t col, uint8_t page)
{
    int ret;

    if ((oled == NULL) || (oled->hal == NULL) || (oled->hal->i2c_write == NULL))
    {
        return -1;
    }

    /* Clamp parameters */
    if (col >= CH1116_COLUMNS)
    {
        col = CH1116_COLUMNS - 1U;
    }
    if (page >= CH1116_PAGES)
    {
        page = CH1116_PAGES - 1U;
    }

    /* Set page address */
    ret = i2c_write_command(oled, CH1116_CMD_SET_PAGE_BASE | page);
    if (ret != 0)
    {
        return ret;
    }

    /* Set column address low nibble */
    ret = i2c_write_command(oled, CH1116_CMD_SET_COL_LOW_BASE | (col & 0x0FU));
    if (ret != 0)
    {
        return ret;
    }

    /* Set column address high nibble */
    ret = i2c_write_command(oled, CH1116_CMD_SET_COL_HIGH_BASE | ((col >> 4U) & 0x0FU));
    if (ret != 0)
    {
        return ret;
    }

    oled->current_col  = col;
    oled->current_page = page;

    return 0;
}

int ch1116_clear_display(ch1116_t *oled)
{
    int ret;

    if ((oled == NULL) || (oled->hal == NULL) || (oled->hal->i2c_write == NULL))
    {
        return -1;
    }

    /* For each page, fill all columns with 0x00 */
    for (uint8_t page = 0U; page < CH1116_PAGES; page++)
    {
        ret = ch1116_set_cursor(oled, 0U, page);
        if (ret != 0)
        {
            return ret;
        }

        for (uint16_t col = 0U; col < CH1116_COLUMNS; col++)
        {
            ret = i2c_write_data(oled, 0x00U);
            if (ret != 0)
            {
                return ret;
            }
        }
    }

    /* Reset cursor */
    oled->current_col  = 0U;
    oled->current_page = 0U;

    return 0;
}

int ch1116_display_string(ch1116_t *oled, const char *str)
{
    int ret;

    if ((oled == NULL) || (oled->hal == NULL) || (oled->hal->i2c_write == NULL) || (str == NULL))
    {
        return -1;
    }

    /* Iterate over each character in the string */
    while (*str != '\0')
    {
        uint8_t ch = (uint8_t)(*str);
        uint8_t index;

        /* Check if character is within font range */
        if ((ch >= FONT_FIRST_CHAR) && (ch <= FONT_LAST_CHAR))
        {
            index = ch - FONT_FIRST_CHAR;
        }
        else
        {
            /* Replace unsupported characters with '?' */
            index = (uint8_t)('?' - FONT_FIRST_CHAR);
        }

        /* Ensure we don't exceed display width (optional clipping) */
        if (oled->current_col + FONT_WIDTH > CH1116_COLUMNS)
        {
            /* Move to next page */
            if (oled->current_page + 1U < CH1116_PAGES)
            {
                ret = ch1116_set_cursor(oled, 0U, oled->current_page + 1U);
                if (ret != 0)
                {
                    return ret;
                }
            }
            else
            {
                /* Display full, stop rendering */
                break;
            }
        }

        /* Write the 5 column bytes of the character */
        for (uint8_t c = 0U; c < FONT_WIDTH; c++)
        {
            ret = i2c_write_data(oled, font_5x7[index][c]);
            if (ret != 0)
            {
                return ret;
            }
            oled->current_col++;
        }

        /* Optionally add a blank column as character spacing */
        if (oled->current_col < CH1116_COLUMNS)
        {
            ret = i2c_write_data(oled, 0x00U);
            if (ret != 0)
            {
                return ret;
            }
            oled->current_col++;
        }

        str++;
    }

    return 0;
}

int ch1116_software_reset(ch1116_t *oled)
{
    if ((oled == NULL) || (oled->hal == NULL) || (oled->hal->i2c_write == NULL))
    {
        return -1;
    }
    return i2c_write_command(oled, CH1116_CMD_SOFT_RESET);
}

int ch1116_deinit(ch1116_t *oled)
{
    int ret;

    if ((oled == NULL) || (oled->hal == NULL) || (oled->hal->i2c_write == NULL))
    {
        return -1;
    }

    /* Turn off display */
    ret = i2c_write_command(oled, CH1116_CMD_DISPLAY_OFF);
    if (ret != 0)
    {
        return ret;
    }

    /* Clear instance state */
    oled->hal          = NULL;
    oled->i2c_addr     = 0U;
    oled->current_col  = 0U;
    oled->current_page = 0U;

    return 0;
}
