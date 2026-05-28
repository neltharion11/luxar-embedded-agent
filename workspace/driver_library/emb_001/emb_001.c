/**
 * @file    emb001.c
 * @brief   Implementation of EMB-001 UART device driver
 *
 * All hardware interactions go through the injected @c emb001_uart_if_t
 * function pointers. No direct HAL references, no printf/malloc/free.
 */
#include "emb001.h"

/* Safety check: force at least one weak definition of the interface */
#if !defined(EMB001_UART_IF_NULL_CHECK)
#define EMB001_UART_IF_NULL_CHECK(iface) do {          \
    if ((iface) == NULL)          return EMB001_ERR_NULL; \
    if ((iface)->uart_init == NULL)   return EMB001_ERR_NULL; \
    if ((iface)->uart_deinit == NULL) return EMB001_ERR_NULL; \
    if ((iface)->uart_transmit == NULL) return EMB001_ERR_NULL; \
    if ((iface)->uart_receive == NULL)  return EMB001_ERR_NULL; \
} while (0)
#endif

int emb001_init(emb001_t *dev)
{
    /* ---- NULL checks ---- */
    if (dev == NULL)
    {
        return EMB001_ERR_NULL;
    }
    EMB001_UART_IF_NULL_CHECK(dev->uart_if);

    /* Delegate UART peripheral initialisation to the transport layer */
    int ret = dev->uart_if->uart_init(dev->uart_param);
    if (ret != 0)
    {
        return ret;
    }

    /* ---- TODO: Add any EMB-001 specific start‑up sequence here ---- */
    /* e.g., send a handshake command, wait for ACK, configure parameters */

    return EMB001_OK;
}

int emb001_send(emb001_t *dev, const uint8_t *data, uint16_t len, uint32_t timeout)
{
    /* ---- NULL checks ---- */
    if (dev == NULL)
    {
        return EMB001_ERR_NULL;
    }
    EMB001_UART_IF_NULL_CHECK(dev->uart_if);
    if (data == NULL)
    {
        return EMB001_ERR_NULL;
    }

    /* Delegate to the transport transmit function */
    return dev->uart_if->uart_transmit(dev->uart_param, data, len, timeout);
}

int emb001_receive(emb001_t *dev, uint8_t *buffer, uint16_t *len, uint32_t timeout)
{
    /* ---- NULL checks ---- */
    if (dev == NULL)
    {
        return EMB001_ERR_NULL;
    }
    EMB001_UART_IF_NULL_CHECK(dev->uart_if);
    if (buffer == NULL)
    {
        return EMB001_ERR_NULL;
    }
    if (len == NULL)
    {
        return EMB001_ERR_NULL;
    }

    /* Delegate to the transport receive function */
    return dev->uart_if->uart_receive(dev->uart_param, buffer, len, timeout);
}

int emb001_deinit(emb001_t *dev)
{
    /* ---- NULL checks ---- */
    if (dev == NULL)
    {
        return EMB001_ERR_NULL;
    }
    EMB001_UART_IF_NULL_CHECK(dev->uart_if);

    /* Delegate UART peripheral de‑initialisation */
    int ret = dev->uart_if->uart_deinit(dev->uart_param);

    /* Clear handle to prevent accidental reuse */
    dev->uart_if   = NULL;
    dev->uart_param = NULL;

    return (ret == 0) ? EMB001_OK : ret;
}
