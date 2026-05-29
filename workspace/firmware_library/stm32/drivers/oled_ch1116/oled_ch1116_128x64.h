/**
 * @file    oled_ch1116.h
 * @brief   I2C OLED driver for CH1116 (ZJY130-2864KSWLG01, 128x64)
 * @note    I2C address: 0x3D (SA0=HIGH).  PB6=SCL, PB7=SDA, PA4=RESET.
 *          CH1116 requires 0x40 control byte for data (NOT 0xC0).
 */

#ifndef OLED_CH1116_H
#define OLED_CH1116_H

#include "stm32f1xx_hal.h"

/* ---- Display geometry ---- */
#define OLED_WIDTH     128
#define OLED_HEIGHT     64
#define OLED_PAGES       8
#define OLED_COL_OFFSET  2

/* ---- I2C ---- */
#define OLED_I2C_ADDR   (0x3D << 1)

/* ---- Control bytes ---- */
#define OLED_CTRL_CMD   0x00  /* Co=0, D/C#=0 */
#define OLED_CTRL_DATA  0x40  /* Co=0, D/C#=1 (CH1116: do NOT use 0xC0!) */

/* ---- GPIO RESET ---- */
#define OLED_RST_PORT   GPIOA
#define OLED_RST_PIN    GPIO_PIN_4

/* ---- Shared I2C handle (defined in app_main.c) ---- */
extern I2C_HandleTypeDef hi2c1;
#define OLED_I2C_HANDLE hi2c1

/* Last data-write error (set by OLED_WriteData). 0 = OK. */
extern volatile int oled_data_err;

/* ---- API ---- */
HAL_StatusTypeDef OLED_Probe(void);
HAL_StatusTypeDef OLED_Init(void);
void                OLED_HWReset(void);
HAL_StatusTypeDef   OLED_WriteCmd(uint8_t cmd);
void                OLED_Clear(void);
void                OLED_ClearPage(uint8_t page);
void                OLED_SetCursor(uint8_t page, uint8_t col);
void                OLED_WriteChar(char ch);
void                OLED_WriteString(const char *str);
void                OLED_Fill(uint8_t pattern);
void                OLED_DrawBitmap(uint8_t x, uint8_t y, uint8_t w, uint8_t h,
                                    const uint8_t *bmp);

#endif /* OLED_CH1116_H */
