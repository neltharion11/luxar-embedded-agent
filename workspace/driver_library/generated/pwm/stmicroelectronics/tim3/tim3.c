/**
 * @file pwm_driver.c
 * @brief Implementation of MCU-agnostic PWM driver.
 */
#include "pwm_driver.h"
#include <stddef.h>

/* ── Public functions ─────────────────────────────────────────── */

int PWM_Device_Init(PWM_Device_t* dev,
                    void*         handle,
                    uint32_t      channel,
                    pwm_start_fn_t   start,
                    pwm_stop_fn_t    stop,
                    pwm_set_duty_fn_t set_duty)
{
    /* Check all pointers */
    if (dev == NULL)
        return PWM_ERR_NULL;
    if (handle == NULL)
        return PWM_ERR_NULL;
    if (start == NULL || stop == NULL || set_duty == NULL)
        return PWM_ERR_NULL;

    dev->handle   = handle;
    dev->channel  = channel;
    dev->start    = start;
    dev->stop     = stop;
    dev->set_duty = set_duty;

    return PWM_OK;
}

int PWM_Device_Start(PWM_Device_t* dev)
{
    if (dev == NULL)
        return PWM_ERR_NULL;
    if (dev->handle == NULL || dev->start == NULL)
        return PWM_ERR_NULL;

    /* Call the injected start function */
    int ret = dev->start(dev->handle, dev->channel);
    return (ret == 0) ? PWM_OK : PWM_ERR_OPFAIL;
}

int PWM_Device_Stop(PWM_Device_t* dev)
{
    if (dev == NULL)
        return PWM_ERR_NULL;
    if (dev->handle == NULL || dev->stop == NULL)
        return PWM_ERR_NULL;

    int ret = dev->stop(dev->handle, dev->channel);
    return (ret == 0) ? PWM_OK : PWM_ERR_OPFAIL;
}

int PWM_Device_SetDuty(PWM_Device_t* dev, uint32_t duty)
{
    if (dev == NULL)
        return PWM_ERR_NULL;
    if (dev->handle == NULL || dev->set_duty == NULL)
        return PWM_ERR_NULL;

    /* Range check: typical ARR=999 gives max duty=999.
       We treat any value >999 as invalid (configurable if needed). */
    if (duty > 999U)
        return PWM_ERR_PARAM;

    int ret = dev->set_duty(dev->handle, dev->channel, duty);
    return (ret == 0) ? PWM_OK : PWM_ERR_OPFAIL;
}

int PWM_Device_DeInit(PWM_Device_t* dev)
{
    if (dev == NULL)
        return PWM_ERR_NULL;

    dev->handle   = NULL;
    dev->channel  = 0;
    dev->start    = NULL;
    dev->stop     = NULL;
    dev->set_duty = NULL;

    return PWM_OK;
}
