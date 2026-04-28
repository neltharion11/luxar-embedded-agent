/**
 * @file    hc_sr04.c
 * @brief   Implementation of MCU-independent HC-SR04 ultrasonic driver.
 * @note    All operations performed via injected HAL function pointers.
 */

#include "hc_sr04.h"
#include <stddef.h>   /* for NULL */

/* ---------------------------------------------------------------------------
 * Internal device structure
 * --------------------------------------------------------------------------- */
struct hc_sr04_device {
    const hc_sr04_hal_t* hal;     /* Must be valid for entire lifetime */
    hc_sr04_cfg_t        cfg;     /* Expanded configuration             */
    uint8_t              busy;    /* Non‑zero while measurement in progress */
};

/* ---------------------------------------------------------------------------
 * Default configuration
 * --------------------------------------------------------------------------- */
static const hc_sr04_cfg_t HC_SR04_DEFAULT_CFG = {
    .timeout_us       = 38000U,   /* 38 ms ~ 6.5 m */
    .trigger_pulse_us = 10U
};

/* ---------------------------------------------------------------------------
 * Parameter validation macro
 * --------------------------------------------------------------------------- */
#define CHECK_NULL(p)   do { if ((p) == NULL) return HC_SR04_ERR_NULL_PTR; } while(0)

/* ---------------------------------------------------------------------------
 * Public functions
 * --------------------------------------------------------------------------- */

hc_sr04_t* hc_sr04_init(const hc_sr04_hal_t* hal, const hc_sr04_cfg_t* cfg)
{
    /* Validate HAL – all function pointers required except crit_enter/exit */
    if (hal == NULL) return NULL;
    if (hal->trig_set == NULL) return NULL;
    if (hal->echo_read == NULL) return NULL;
    if (hal->get_us   == NULL) return NULL;
    if (hal->delay_us == NULL) return NULL;

    /* Allocate device (static allocation to avoid malloc) */
    static hc_sr04_t s_dev;      /* single‑instance – safe for MCU‑style usage */
    hc_sr04_t* dev = &s_dev;

    dev->hal  = hal;
    dev->busy = 0U;

    if (cfg != NULL) {
        dev->cfg = *cfg;
    } else {
        dev->cfg = HC_SR04_DEFAULT_CFG;
    }

    return dev;
}

int hc_sr04_deinit(hc_sr04_t* dev)
{
    CHECK_NULL(dev);
    dev->busy = 0U;
    /* No dynamic memory to free; static instance remains. */
    return HC_SR04_OK;
}

int hc_sr04_trigger(hc_sr04_t* dev)
{
    CHECK_NULL(dev);
    CHECK_NULL(dev->hal);

    if (dev->busy) {
        return HC_SR04_ERR_BUSY;
    }

    const hc_sr04_hal_t* hal = dev->hal;

    /* Generate TRIG pulse */
    hal->trig_set(1U);                       /* high */
    hal->delay_us(dev->cfg.trigger_pulse_us);/* pulse width */
    hal->trig_set(0U);                       /* low  */

    /* Mark busy – ECHO will follow */
    dev->busy = 1U;

    return HC_SR04_OK;
}

int hc_sr04_read_distance(hc_sr04_t* dev, uint32_t* distance_mm)
{
    CHECK_NULL(dev);
    CHECK_NULL(dev->hal);
    CHECK_NULL(distance_mm);

    const hc_sr04_hal_t* hal = dev->hal;
    uint32_t timeout;
    uint32_t start_us;
    uint32_t echo_start;
    uint32_t echo_end;
    uint32_t pulse_us;

    /* Wait for ECHO to go high (start of pulse) */
    timeout = dev->cfg.timeout_us;
    while (hal->echo_read() == 0U) {
        if (timeout == 0U) {
            dev->busy = 0U;
            return HC_SR04_ERR_TIMEOUT;
        }
        hal->delay_us(1U);
        timeout--;
    }

    /* Record start time with disabled interrupts if possible */
    if (hal->crit_enter) hal->crit_enter();
    echo_start = hal->get_us();
    if (hal->crit_exit) hal->crit_exit();

    /* Wait for ECHO to go low */
    timeout = dev->cfg.timeout_us;
    while (hal->echo_read() != 0U) {
        if (timeout == 0U) {
            dev->busy = 0U;
            return HC_SR04_ERR_NO_ECHO;
        }
        hal->delay_us(1U);
        timeout--;
    }

    /* Record end time */
    if (hal->crit_enter) hal->crit_enter();
    echo_end = hal->get_us();
    if (hal->crit_exit) hal->crit_exit();

    /* Compute pulse duration (handle wrap‑around if time is 32‑bit) */
    if (echo_end >= echo_start) {
        pulse_us = echo_end - echo_start;
    } else {
        /* 32‑bit wrap – assume only one wrap; distance<~6.5m ~38ms */
        pulse_us = (UINT32_MAX - echo_start) + echo_end + 1U;
    }

    dev->busy = 0U;

    /* Convert pulse width to distance:
     *   distance (mm) = pulse_us * 340 m/s / 2 / 1000
     *                 = pulse_us * 0.17 mm/us
     * Use fixed‑point: multiply by 17 / 100
     *   pulse_us * 17 / 100  gives mm (truncated).
     */
    *distance_mm = (pulse_us * 17U) / 100U;

    return HC_SR04_OK;
}

int hc_sr04_measure(hc_sr04_t* dev, uint32_t* distance_mm)
{
    CHECK_NULL(dev);

    int ret = hc_sr04_trigger(dev);
    if (ret != HC_SR04_OK) return ret;

    return hc_sr04_read_distance(dev, distance_mm);
}
