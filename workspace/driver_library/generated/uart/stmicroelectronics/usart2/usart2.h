/**
 * @file drv_uart.h
 * @brief MCU-independent UART driver interface.
 *
 * This driver provides an abstract UART interface that decouples the
 * application from the underlying HAL implementation.  All HAL operations
 * are accessed through a function-pointer table (@ref UART_Ops) that must
 * be initialised before use.
 *
 * A default implementation for STM32 (HAL) is provided in the companion
 * source file.  Users may replace it with platform-specific adapters.
 *
 * @note No global HAL handles (e.g. huart1) are directly referenced inside
 *       the driver – all operations are performed through the supplied
 *       handle pointer (void*).
 */

#ifndef DRV_UART_H
#define DRV_UART_H

#include <stdint.h>   /* uint8_t, uint16_t, uint32_t */
#include <stddef.h>   /* NULL */

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Return codes for UART driver functions. */
#define DRV_UART_OK         0   /**< Operation succeeded. */
#define DRV_UART_ERR_NULL   (-1)  /**< NULL pointer parameter. */
#define DRV_UART_ERR_INIT   (-2)  /**< Initialisation failed. */
#define DRV_UART_ERR_TX     (-3)  /**< Transmit failed. */
#define DRV_UART_ERR_RX     (-4)  /**< Receive failed. */
#define DRV_UART_ERR_PARAM  (-5)  /**< Invalid parameter (e.g. length=0). */

/**
 * @brief UART operation table.
 *
 * The application must populate this structure with pointers to platform-
 * specific functions, then call the corresponding wrappers.
 *
 * Example (STM32):
 * @code
 *   UART_Ops uart_ops;
 *   drv_uart_get_stm32_ops(&uart_ops);
 *   UART_HandleTypeDef huart2;
 *   uart_ops.init(&huart2);
 *   uart_ops.transmit(&huart2, data, len, 100);
 * @endcode
 */
typedef struct {
    /**
     * @brief Initialise the UART peripheral.
     * @param uart_handle   Pointer to the UART handle (platform-specific).
     * @return 0 on success, negative error code on failure.
     */
    int (*init)(void *uart_handle);

    /**
     * @brief Transmit a block of data (blocking).
     * @param uart_handle   Pointer to the UART handle.
     * @param data          Pointer to the data buffer.
     * @param len           Number of bytes to transmit.
     * @param timeout       Timeout in milliseconds.
     * @return 0 on success, negative error code on failure.
     */
    int (*transmit)(void *uart_handle, const uint8_t *data, uint16_t len, uint32_t timeout);

    /**
     * @brief Receive a block of data (blocking).
     * @param uart_handle   Pointer to the UART handle.
     * @param data          Pointer to the receive buffer.
     * @param len           Number of bytes to receive.
     * @param timeout       Timeout in milliseconds.
     * @return 0 on success, negative error code on failure.
     */
    int (*receive)(void *uart_handle, uint8_t *data, uint16_t len, uint32_t timeout);

    /**
     * @brief Deinitialise the UART peripheral.
     * @param uart_handle   Pointer to the UART handle.
     * @return 0 on success, negative error code on failure.
     */
    int (*deinit)(void *uart_handle);
} UART_Ops;

/**
 * @brief Get the default STM32 (HAL) UART operations.
 *
 * This function populates the provided ops structure with pointers to
 * internal STM32 HAL implementations.  The handle passed to the ops
 * must be a valid pointer to a UART_HandleTypeDef.
 *
 * @param[out] ops   Pointer to a UART_Ops structure to fill.
 * @return 0 on success, @ref DRV_UART_ERR_NULL if ops is NULL.
 */
int drv_uart_get_stm32_ops(UART_Ops *ops);

#ifdef __cplusplus
}
#endif

#endif /* DRV_UART_H */
