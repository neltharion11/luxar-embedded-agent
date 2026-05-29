/**
 * @file    oled_ch1116.c
 * @brief   CH1116 128x64 I2C OLED driver (ZJY130-2864KSWLG01).
 *
 * I2C1: PB6=SCL, PB7=SDA, addr 0x3D.  RESET: PA4.
 *
 * Control bytes: 0x00 = command, 0x40 = data.
 * Every I2C transaction MUST start with a control byte.
 */

#include "oled_ch1116.h"
#include "font5x7.h"

volatile int oled_data_err = 0;

/* ---- I2C helpers ---- */

/* Full I2C peripheral reset. Needed after NACK or bus hang. */
static void OLED_I2C_Reset(void)
{
    CLEAR_BIT(OLED_I2C_HANDLE.Instance->CR1, I2C_CR1_PE);
    for (volatile int i = 0; i < 100; i++) { __asm__ volatile("nop"); }
    SET_BIT(OLED_I2C_HANDLE.Instance->CR1, I2C_CR1_PE);
}

/* Send 1 control-byte + up to 128 data bytes in one I2C transaction. */
static HAL_StatusTypeDef OLED_I2C_Write(uint8_t ctrl, const uint8_t *data, uint16_t n)
{
    if (n > 128) return HAL_ERROR;
    uint8_t buf[129];
    buf[0] = ctrl;
    for (uint16_t i = 0; i < n; i++) buf[i + 1] = data[i];
    OLED_I2C_Reset();
    return HAL_I2C_Master_Transmit(&OLED_I2C_HANDLE, OLED_I2C_ADDR,
                                   buf, n + 1U, 50U);
}

/* ---- Public API ---- */

void OLED_HWReset(void)
{
    GPIO_InitTypeDef gpio = {0};
    gpio.Pin   = OLED_RST_PIN;
    gpio.Mode  = GPIO_MODE_OUTPUT_PP;
    gpio.Speed = GPIO_SPEED_FREQ_LOW;
    gpio.Pull  = GPIO_PULLUP;
    HAL_GPIO_Init(OLED_RST_PORT, &gpio);

    HAL_GPIO_WritePin(OLED_RST_PORT, OLED_RST_PIN, GPIO_PIN_SET);
    HAL_Delay(1);
    HAL_GPIO_WritePin(OLED_RST_PORT, OLED_RST_PIN, GPIO_PIN_RESET);
    HAL_Delay(10);
    HAL_GPIO_WritePin(OLED_RST_PORT, OLED_RST_PIN, GPIO_PIN_SET);
    HAL_Delay(120);
}

/* Send ONE command byte (control byte 0x00 + cmd in one I2C frame). */
HAL_StatusTypeDef OLED_WriteCmd(uint8_t cmd)
{
    uint8_t buf[2] = { OLED_CTRL_CMD, cmd };
    OLED_I2C_Reset();
    return HAL_I2C_Master_Transmit(&OLED_I2C_HANDLE, OLED_I2C_ADDR,
                                   buf, 2, 10);
}

HAL_StatusTypeDef OLED_Probe(void)
{
    uint8_t dummy = OLED_CTRL_CMD;
    OLED_I2C_Reset();
    return HAL_I2C_Master_Transmit(&OLED_I2C_HANDLE, OLED_I2C_ADDR,
                                   &dummy, 1, 10);
}

HAL_StatusTypeDef OLED_Init(void)
{
    HAL_StatusTypeDef rc;
    int step = 0;

    OLED_HWReset();
    OLED_I2C_Reset();
    HAL_Delay(5);

    step = 1;  rc = OLED_WriteCmd(0xAE); if (rc) return -step; HAL_Delay(2);
    step = 2;  rc = OLED_WriteCmd(0xD5); if (rc) return -step;
               rc = OLED_WriteCmd(0x80); if (rc) return -step;
    step = 3;  rc = OLED_WriteCmd(0xA8); if (rc) return -step;
               rc = OLED_WriteCmd(0x3F); if (rc) return -step;
    step = 4;  rc = OLED_WriteCmd(0xD3); if (rc) return -step;
               rc = OLED_WriteCmd(0x00); if (rc) return -step;
    step = 5;  rc = OLED_WriteCmd(0x40); if (rc) return -step;
    step = 6;  rc = OLED_WriteCmd(0xA1); if (rc) return -step;
    step = 7;  rc = OLED_WriteCmd(0xC8); if (rc) return -step;
    step = 8;  rc = OLED_WriteCmd(0xDA); if (rc) return -step;
               rc = OLED_WriteCmd(0x12); if (rc) return -step;
    step = 9;  rc = OLED_WriteCmd(0x81); if (rc) return -step;
               rc = OLED_WriteCmd(0x7F); if (rc) return -step;
    step = 10; rc = OLED_WriteCmd(0xD9); if (rc) return -step;
               rc = OLED_WriteCmd(0xF1); if (rc) return -step;
    step = 11; rc = OLED_WriteCmd(0xDB); if (rc) return -step;
               rc = OLED_WriteCmd(0x40); if (rc) return -step;
    step = 12; rc = OLED_WriteCmd(0xA4); if (rc) return -step;
    step = 13; rc = OLED_WriteCmd(0xA6); if (rc) return -step;
    step = 14; rc = OLED_WriteCmd(0x2E); if (rc) return -step;
    step = 15; rc = OLED_WriteCmd(0xAF); if (rc) return -step;

    return HAL_OK;
}

/* ---- Drawing primitives ---- */

/*
 * Clear entire GDDRAM (all 8 pages × 128 columns).
 * Each page: set page register → set column → write 128 zero data bytes.
 */
void OLED_Clear(void)
{
    oled_data_err = 0;

    for (uint8_t p = 0; p < OLED_PAGES && oled_data_err == 0; p++) {
        OLED_WriteCmd(0xB0 | p);
        OLED_WriteCmd(0x00 | OLED_COL_OFFSET);
        OLED_WriteCmd(0x10);

        static const uint8_t zero[128] = {0};
        HAL_StatusTypeDef rc = OLED_I2C_Write(OLED_CTRL_DATA, zero, 128);
        if (rc != HAL_OK) { oled_data_err = (int)rc; return; }
    }
}

/*
 * Clear a single page (0..7), column 0..127.
 */
void OLED_ClearPage(uint8_t page)
{
    oled_data_err = 0;
    page &= 0x07;

    OLED_WriteCmd(0xB0 | page);
    OLED_WriteCmd(0x00 | OLED_COL_OFFSET);
    OLED_WriteCmd(0x10);

    static const uint8_t zero[128] = {0};
    HAL_StatusTypeDef rc = OLED_I2C_Write(OLED_CTRL_DATA, zero, 128);
    if (rc != HAL_OK) { oled_data_err = (int)rc; }
}

void OLED_SetCursor(uint8_t page, uint8_t col)
{
    OLED_WriteCmd(0xB0 | (page & 0x07));
    col += OLED_COL_OFFSET;
    OLED_WriteCmd(0x00 | (col & 0x0F));
    OLED_WriteCmd(0x10 | ((col >> 4) & 0x0F));
}

/*
 * Write ONE character: 5 font columns + 1 spacer in a single transaction.
 * Column auto-increment works within the transaction.
 */
void OLED_WriteChar(char ch)
{
    if (ch < 0x20 || ch > 0x7F) ch = ' ';
    uint16_t idx = (uint16_t)(ch - 0x20);

    uint8_t buf[6];
    buf[0] = font5x7[idx][0];
    buf[1] = font5x7[idx][1];
    buf[2] = font5x7[idx][2];
    buf[3] = font5x7[idx][3];
    buf[4] = font5x7[idx][4];
    buf[5] = 0x00;  /* 1-pixel inter-char gap */

    HAL_StatusTypeDef rc = OLED_I2C_Write(OLED_CTRL_DATA, buf, 6);
    if (rc != HAL_OK) { oled_data_err = (int)rc; }
}

/*
 * Write an entire string in ONE I2C transaction.
 * No STOP between characters — column auto-increment across the whole string.
 */
void OLED_WriteString(const char *str)
{
    if (str == NULL) return;
    oled_data_err = 0;

    static uint8_t buf[128];
    uint16_t len = 0;

    while (*str && len + 6 <= sizeof(buf)) {
        char ch = *str;
        if (ch < 0x20 || ch > 0x7F) ch = ' ';
        uint16_t idx = (uint16_t)(ch - 0x20);
        buf[len++] = font5x7[idx][0];
        buf[len++] = font5x7[idx][1];
        buf[len++] = font5x7[idx][2];
        buf[len++] = font5x7[idx][3];
        buf[len++] = font5x7[idx][4];
        buf[len++] = 0x00;
        str++;
    }

    if (len > 0) {
        HAL_StatusTypeDef rc = OLED_I2C_Write(OLED_CTRL_DATA, buf, len);
        if (rc != HAL_OK) { oled_data_err = (int)rc; }
    }
}

void OLED_Fill(uint8_t pattern)
{
    oled_data_err = 0;
    for (uint8_t p = 0; p < OLED_PAGES && oled_data_err == 0; p++) {
        OLED_SetCursor(p, 0);
        for (uint8_t c = 0; c < 128 && oled_data_err == 0; c++) {
            uint8_t tmp[2] = { OLED_CTRL_DATA, pattern };
            OLED_I2C_Reset();
            HAL_StatusTypeDef rc = HAL_I2C_Master_Transmit(
                &OLED_I2C_HANDLE, OLED_I2C_ADDR, tmp, 2, 10);
            if (rc != HAL_OK) { oled_data_err = (int)rc; return; }
        }
    }
}

void OLED_DrawBitmap(uint8_t x, uint8_t y, uint8_t w, uint8_t h,
                     const uint8_t *bmp)
{
    if (bmp == NULL) return;
    if (w == 0 || h == 0) return;

    uint8_t start_page = y / 8;
    uint8_t end_page   = (y + h - 1) / 8;
    for (uint8_t p = start_page; p <= end_page && p < OLED_PAGES && oled_data_err == 0; p++) {
        OLED_SetCursor(p, x);
        for (uint8_t col = 0; col < w && (x + col) < OLED_WIDTH && oled_data_err == 0; col++) {
            uint8_t b = 0;
            for (uint8_t bit = 0; bit < 8; bit++) {
                uint8_t py = p * 8 + bit;
                if (py >= y && py < (y + h)) {
                    uint16_t bmp_idx = (uint16_t)(py - y) * w + col;
                    if (bmp[bmp_idx >> 3] & (1 << (bmp_idx & 7)))
                        b |= (1 << bit);
                }
            }
            uint8_t tmp[2] = { OLED_CTRL_DATA, b };
            OLED_I2C_Reset();
            HAL_StatusTypeDef rc = HAL_I2C_Master_Transmit(
                &OLED_I2C_HANDLE, OLED_I2C_ADDR, tmp, 2, 10);
            if (rc != HAL_OK) { oled_data_err = (int)rc; return; }
        }
    }
}
