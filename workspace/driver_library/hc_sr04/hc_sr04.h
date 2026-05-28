/**
 * @file    hc_sr04.h
 * @brief   MCU-independent driver for HC-SR04 ultrasonic distance sensor.
 *          Uses injected HAL function pointers for GPIO and timing.
 * @note    No direct HAL handles, printf, malloc, or free are used.
 */

#ifndef HC_SR04_H
#define HC_SR04_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---------------------------------------------------------------------------
 * Error codes (returned by public functions)
 * ------------------------------------------------------------------------- */
#define HC_SR04_OK                 0
#define HC_SR04_ERR_NULL_PTR      -1
#define HC_SR04_ERR_TIMEOUT       -2
#define HC_SR04_ERR_NO_ECHO       -3
#define HC_SR04_ERR_BUSY          -4

/* ---------------------------------------------------------------------------
 * HAL interface structure – inject platform‑specific implementations
 * ------------------------------------------------------------------------- */
typedef struct {
    /**
     * @brief Set TRIG pin to high or low.
     * @param state  0 = low, any non‑zero = high
     */
    void (*trig_set)(uint8_t state);

    /**
     * @brief Read the current state of the ECHO pin.
     * @return 0 = low, non‑zero = high
     */
    uint8_t (*echo_read)(void);

    /**
     * @brief Get current microsecond counter value (free‑running).
     * @return Tick value in microseconds.
     */
    uint32_t (*get_us)(void);

    /**
     * @brief Microsecond delay (blocking).
     * @param us  Delay duration in microseconds.
     */
    void (*delay_us)(uint32_t us);

    /**
     * @brief (Optional) Critical‑section enter / leave for timing accuracy.
     *        May be set to NULL if not required.
     */
    void (*crit_enter)(void);
    void (*crit_exit)(void);
} hc_sr04_hal_t;

/* ---------------------------------------------------------------------------
 * Configuration structure (optional, may be extended)
 * ------------------------------------------------------------------------- */
typedef struct {
    uint32_t timeout_us;         /**< Maximum echo wait in microseconds (default 38000) */
    uint32_t trigger_pulse_us;   /**< TRIG pulse width in microseconds (default 10)    */
} hc_sr04_cfg_t;

/* ---------------------------------------------------------------------------
 * Opaque handle (user must not dereference)
 * ------------------------------------------------------------------------- */
typedef struct hc_sr04_device hc_sr04_t;

/* ---------------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------------- */

/**
 * @brief  Initialize a new HC-SR04 driver instance.
 * @param  hal   Pointer to filled HAL interface (must stay valid).
 * @param  cfg   Optional configuration (NULL = use defaults).
 * @return Pointer to device handle, or NULL on failure (invalid HAL, memory, etc.).
 */
hc_sr04_t* hc_sr04_init(const hc_sr04_hal_t* hal, const hc_sr04_cfg_t* cfg);

/**
 * @brief  Deinitialize and free resources.
 * @param  dev  Device handle returned by init.
 * @return HC_SR04_OK on success, negative on error.
 */
int hc_sr04_deinit(hc_sr04_t* dev);

/**
 * @brief  Send a trigger pulse to start a measurement.
 * @param  dev  Device handle.
 * @return HC_SR04_OK on success, negative on error (e.g. device busy).
 * @note   After this function returns, the ECHO pin will go high.
 *         Call hc_sr04_read_distance to obtain the measured pulse width.
 */
int hc_sr04_trigger(hc_sr04_t* dev);

/**
 * @brief  Read the measured distance (blocking).
 * @param  dev       Device handle.
 * @param  distance_mm  Output distance in millimeters.
 * @return HC_SR04_OK on success, negative on error (timeout, no echo, etc.).
 */
int hc_sr04_read_distance(hc_sr04_t* dev, uint32_t* distance_mm);

/**
 * @brief  Convenience: trigger + read distance in one call.
 * @param  dev       Device handle.
 * @param  distance_mm  Output distance in millimeters.
 * @return HC_SR04_OK on success, negative on error.
 */
int hc_sr04_measure(hc_sr04_t* dev, uint32_t* distance_mm);

#ifdef __cplusplus
}
#endif

#endif /* HC_SR04_H */
