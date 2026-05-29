/**
 * @file    aht20.c
 * @brief   AHT20 I2C driver implementation.
 *
 * Protocol (from Aosong AHT20 datasheet):
 *   1. Wait 40 ms after power-up.
 *   2. Send init command 0xBE (calibration).
 *   3. Trigger: write {0xAC, 0x33, 0x00}, wait 80 ms.
 *   4. Read 7 bytes: status + 6 data bytes.
 *   5. Humidity: RH = (raw_h / 2^20) * 100%
 *   6. Temperature: T = (raw_t / 2^20) * 200 - 50
 */

#include "aht20.h"

/**
 * @brief  Initialize AHT20 sensor (send calibration command).
 * @param[in]  hi2c  Pointer to initialized I2C handle.
 * @retval HAL_OK on success, HAL_ERROR if hi2c is NULL, or I2C error.
 */
HAL_StatusTypeDef AHT20_Init(I2C_HandleTypeDef *hi2c)
{
    HAL_StatusTypeDef rc;
    uint8_t buf[3];

    if (hi2c == NULL) { return HAL_ERROR; }

    CLEAR_BIT(hi2c->Instance->CR1, I2C_CR1_PE);
    for (volatile int i = 0; i < 100; i++) { __asm__ volatile("nop"); }
    SET_BIT(hi2c->Instance->CR1, I2C_CR1_PE);

    HAL_Delay(40);

    buf[0] = 0xBE;
    buf[1] = 0x08;
    buf[2] = 0x00;
    rc = HAL_I2C_Master_Transmit(hi2c, AHT20_I2C_ADDR, buf, 3, 20);
    if (rc != HAL_OK) return rc;

    HAL_Delay(10);
    return HAL_OK;
}

/**
 * @brief  Read temperature and humidity from AHT20.
 * @param[in]  hi2c    Pointer to initialized I2C handle.
 * @param[out] temp_c  Temperature in degrees Celsius.
 * @param[out] hum_pct Relative humidity in percent.
 * @retval HAL_OK on success.
 * @retval HAL_ERROR if any pointer is NULL.
 * @retval HAL_BUSY if sensor is unresponsive.
 */
HAL_StatusTypeDef AHT20_Read(I2C_HandleTypeDef *hi2c, float *temp_c, float *hum_pct)
{
    HAL_StatusTypeDef rc;
    uint8_t cmd[3] = {0xAC, 0x33, 0x00};
    uint8_t data[7] = {0};

    if (hi2c == NULL)   { return HAL_ERROR; }
    if (temp_c == NULL) { return HAL_ERROR; }
    if (hum_pct == NULL){ return HAL_ERROR; }

    rc = HAL_I2C_Master_Transmit(hi2c, AHT20_I2C_ADDR, cmd, 3, 20);
    if (rc != HAL_OK) return rc;

    HAL_Delay(80);

    rc = HAL_I2C_Master_Receive(hi2c, AHT20_I2C_ADDR, data, 7, 20);
    if (rc != HAL_OK) return rc;

    if (data[0] & 0x80) {
        HAL_Delay(30);
        rc = HAL_I2C_Master_Receive(hi2c, AHT20_I2C_ADDR, data, 7, 20);
        if (rc != HAL_OK) return rc;
        if (data[0] & 0x80) return HAL_BUSY;
    }

    uint32_t raw_h = ((uint32_t)data[1] << 12)
                   | ((uint32_t)data[2] << 4)
                   | ((uint32_t)data[3] >> 4);

    uint32_t raw_t = ((uint32_t)(data[3] & 0x0F) << 16)
                   | ((uint32_t)data[4] << 8)
                   |  (uint32_t)data[5];

    *hum_pct = (float)raw_h * 100.0f / 1048576.0f;
    *temp_c  = (float)raw_t * 200.0f / 1048576.0f - 50.0f;

    return HAL_OK;
}
