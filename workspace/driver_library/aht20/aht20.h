/**
 * @file    aht20.h
 * @brief   AHT20 I2C temperature & humidity sensor driver.
 * @note    I2C address: 0x38.  Shares I2C bus with OLED.
 */

#ifndef AHT20_H
#define AHT20_H

#include "stm32f1xx_hal.h"
#include <stdint.h>

/* I2C address (7-bit shifted) */
#define AHT20_I2C_ADDR   (0x38 << 1)

/* ---- API ---- */

/** Initialize sensor: check status, calibrate if needed. Call once after I2C ready. */
HAL_StatusTypeDef AHT20_Init(I2C_HandleTypeDef *hi2c);

/**
 * Read temperature and humidity.
 * @param hi2c    I2C handle (shared with OLED on hi2c1)
 * @param temp_c  [out] Temperature in °C
 * @param hum_pct [out] Relative humidity in %
 * @return HAL_OK on success
 */
HAL_StatusTypeDef AHT20_Read(I2C_HandleTypeDef *hi2c, float *temp_c, float *hum_pct);

#endif /* AHT20_H */
