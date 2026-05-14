/**
 * @file drv_uart.c
 * @brief Default STM32 (HAL) implementation of the UART driver interface.
 *
 * This file implements the UART_Ops table for STM32 using the HAL library.
 * All HAL operations are accessed through the function pointers provided
 * by drv_uart_get_stm32_ops().  The user supplies a UART_HandleTypeDef
 * pointer as the opaque handle.
 *
 * @note No global HAL handles are used.
 */

#include "drv_uart.h"
#include "stm32f1xx_hal.h"   /* HAL_UART_Transmit, HAL_UART_Receive etc. */

/* -------------------------------------------------------------------------
 * Internal (private) helper to validate handle pointer
 * ------------------------------------------------------------------------- */
static inline int validate_non_null(const void *ptr)
{
    return (ptr == NULL) ? DRV_UART_ERR_NULL : DRV_UART_OK;
}

/* -------------------------------------------------------------------------
 * STM32 HAL implementations of the UART ops
 * ------------------------------------------------------------------------- */

/**
 * @brief STM32 implementation of UART init.
 * @note  Initialisation (clock, GPIO, NVIC) is assumed to be done outside the
 *        driver (e.g. in CubeMX-generated code).  This function only validates
 *        the handle and is provided for symmetry.
 */
static int uart_stm32_init(void *uart_handle)
{
    int ret = validate_non_null(uart_handle);
    if (ret != DRV_UART_OK) {
        return ret;
    }

    /* The handle is assumed to be already initialised by HAL_UART_Init().
     * We only check that the instance is not NULL as a basic sanity test. */
    UART_HandleTypeDef *huart = (UART_HandleTypeDef *)uart_handle;
    if (huart->Instance == NULL) {
        return DRV_UART_ERR_INIT;
    }
    return DRV_UART_OK;
}

/**
 * @brief STM32 implementation of UART transmit (blocking).
 */
static int uart_stm32_transmit(void *uart_handle, const uint8_t *data, uint16_t len, uint32_t timeout)
{
    int ret = validate_non_null(uart_handle);
    if (ret != DRV_UART_OK) {
        return ret;
    }
    if (data == NULL) {
        return DRV_UART_ERR_NULL;
    }
    if (len == 0) {
        return DRV_UART_ERR_PARAM;
    }

    UART_HandleTypeDef *huart = (UART_HandleTypeDef *)uart_handle;
    HAL_StatusTypeDef status = HAL_UART_Transmit(huart, (uint8_t *)data, len, timeout);
    return (status == HAL_OK) ? DRV_UART_OK : DRV_UART_ERR_TX;
}

/**
 * @brief STM32 implementation of UART receive (blocking).
 */
static int uart_stm32_receive(void *uart_handle, uint8_t *data, uint16_t len, uint32_t timeout)
{
    int ret = validate_non_null(uart_handle);
    if (ret != DRV_UART_OK) {
        return ret;
    }
    if (data == NULL) {
        return DRV_UART_ERR_NULL;
    }
    if (len == 0) {
        return DRV_UART_ERR_PARAM;
    }

    UART_HandleTypeDef *huart = (UART_HandleTypeDef *)uart_handle;
    HAL_StatusTypeDef status = HAL_UART_Receive(huart, data, len, timeout);
    return (status == HAL_OK) ? DRV_UART_OK : DRV_UART_ERR_RX;
}

/**
 * @brief STM32 implementation of UART deinit.
 */
static int uart_stm32_deinit(void *uart_handle)
{
    int ret = validate_non_null(uart_handle);
    if (ret != DRV_UART_OK) {
        return ret;
    }

    UART_HandleTypeDef *huart = (UART_HandleTypeDef *)uart_handle;
    HAL_StatusTypeDef status = HAL_UART_DeInit(huart);
    return (status == HAL_OK) ? DRV_UART_OK : DRV_UART_ERR_INIT;
}

/* -------------------------------------------------------------------------
 * Public function: fill ops table with STM32 implementations
 * ------------------------------------------------------------------------- */
int drv_uart_get_stm32_ops(UART_Ops *ops)
{
    if (ops == NULL) {
        return DRV_UART_ERR_NULL;
    }

    ops->init     = uart_stm32_init;
    ops->transmit = uart_stm32_transmit;
    ops->receive  = uart_stm32_receive;
    ops->deinit   = uart_stm32_deinit;

    return DRV_UART_OK;
}
