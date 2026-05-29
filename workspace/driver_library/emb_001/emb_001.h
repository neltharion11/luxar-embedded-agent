/**
 * @file    emb001.h
 * @brief   MCU-agnostic device driver for EMB-001 over UART
 *
 * This driver defines an abstract UART transport interface that must be
 * provided by the application layer. All hardware-specific HAL details
 * are hidden behind function pointers.
 *
 * @note    No direct HAL handle references, no malloc/free/printf.
 */
#ifndef EMB001_H
#define EMB001_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* --------------------------------------------------------------------------
 * Error codes (negative values)
 * -------------------------------------------------------------------------- */
#define EMB001_OK           0
#define EMB001_ERR_NULL    -1
#define EMB001_ERR_TIMEOUT -2
#define EMB001_ERR_COMM    -3
#define EMB001_ERR_INVALID -4

/**
 * @brief  UART transport interface (injected by application).
 *
 * Every call receives a `param` pointer (e.g., a struct containing the
 * actual HAL handle). This keeps the driver completely platform‑independent.
 */
typedef struct {
    /**
     * @brief   Initialize the UART peripheral.
     * @param   param  opaque pointer to platform-specific UART context
     * @return  0 on success, negative error code on failure
     */
    int (*uart_init)(void *param);

    /**
     * @brief   Transmit a buffer over UART (blocking).
     * @param   param   opaque pointer to platform-specific UART context
     * @param   data    pointer to data to send
     * @param   len     number of bytes to send
     * @param   timeout max time in ms to wait for transmission to complete
     * @return  0 on success, EMB001_ERR_TIMEOUT on timeout, other on failure
     */
    int (*uart_transmit)(void *param, const uint8_t *data, uint16_t len, uint32_t timeout);

    /**
     * @brief   Receive a buffer over UART (blocking).
     * @param   param   opaque pointer to platform-specific UART context
     * @param   data    pointer to receive buffer
     * @param   len     [in] max bytes to read; [out] actually received bytes
     * @param   timeout max time in ms to wait for first byte
     * @return  0 on success, EMB001_ERR_TIMEOUT on timeout, other on failure
     */
    int (*uart_receive)(void *param, uint8_t *data, uint16_t *len, uint32_t timeout);

    /**
     * @brief   Deinitialize the UART peripheral.
     * @param   param  opaque pointer to platform-specific UART context
     * @return  0 on success, negative error code on failure
     */
    int (*uart_deinit)(void *param);
} emb001_uart_if_t;

/**
 * @brief  EMB-001 device handle.
 *
 * The user fills @c uart_if and @c uart_param before calling @ref emb001_init.
 */
typedef struct {
    const emb001_uart_if_t *uart_if;  /**< UART transport function table  */
    void                   *uart_param; /**< Opaque pointer to UART context */
} emb001_t;

/**
 * @brief   Initialize the EMB-001 device.
 *
 * Calls uart_if->uart_init() and performs any chip‑specific startup sequence.
 *
 * @param   dev  pointer to EMB-001 device handle (must be non‑NULL)
 * @return  0 on success, negative error code on failure
 * @retval  EMB001_ERR_NULL  if @c dev or its interface table is NULL
 */
int emb001_init(emb001_t *dev);

/**
 * @brief   Send raw data to the EMB-001 device.
 *
 * @param   dev      pointer to initialised EMB-001 device handle
 * @param   data     pointer to data bytes to send
 * @param   len      number of bytes to send
 * @param   timeout  max time in ms for the transmission to complete
 * @return  0 on success, negative error code on failure
 */
int emb001_send(emb001_t *dev, const uint8_t *data, uint16_t len, uint32_t timeout);

/**
 * @brief   Receive raw data from the EMB-001 device.
 *
 * @param   dev      pointer to initialised EMB-001 device handle
 * @param   buffer   pointer to receive buffer
 * @param   len      [in] max bytes to read; [out] actually received bytes
 * @param   timeout  max time in ms to wait for the first byte
 * @return  0 on success, negative error code on failure
 */
int emb001_receive(emb001_t *dev, uint8_t *buffer, uint16_t *len, uint32_t timeout);

/**
 * @brief   Deinitialize the EMB-001 device.
 *
 * Calls uart_if->uart_deinit() and resets the handle.
 *
 * @param   dev  pointer to EMB-001 device handle
 * @return  0 on success, negative error code on failure
 */
int emb001_deinit(emb001_t *dev);

#ifdef __cplusplus
}
#endif

#endif /* EMB001_H */
