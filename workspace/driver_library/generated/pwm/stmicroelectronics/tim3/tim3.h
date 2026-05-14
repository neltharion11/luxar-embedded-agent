/**
 * @file pwm_driver.h
 * @brief MCU-agnostic PWM driver using function pointer injection
 *
 * This driver abstracts PWM channel control (start, stop, set duty cycle)
 * behind an interface struct. No direct HAL references are used.
 */
#ifndef PWM_DRIVER_H
#define PWM_DRIVER_H

#include <stdint.h>

/* Error codes */
#define PWM_OK          0
#define PWM_ERR_NULL    (-1)   /* NULL pointer argument */
#define PWM_ERR_PARAM   (-2)   /* Invalid parameter (e.g. duty out of range) */
#define PWM_ERR_OPFAIL  (-3)   /* Underlying operation failed */

/**
 * @brief Function pointer type for starting PWM output on a channel.
 * @param handle  Opaque pointer to peripheral handle (e.g. TIM_HandleTypeDef*)
 * @param channel Channel identifier (e.g. TIM_CHANNEL_1)
 * @return 0 on success, negative error code on failure
 */
typedef int (*pwm_start_fn_t)(void* handle, uint32_t channel);

/**
 * @brief Function pointer type for stopping PWM output on a channel.
 * @param handle  Opaque pointer to peripheral handle
 * @param channel Channel identifier
 * @return 0 on success, negative error code on failure
 */
typedef int (*pwm_stop_fn_t)(void* handle, uint32_t channel);

/**
 * @brief Function pointer type for setting PWM duty cycle on a channel.
 * @param handle  Opaque pointer to peripheral handle
 * @param channel Channel identifier
 * @param duty    Duty cycle value (interpretation depends on timer resolution)
 * @return 0 on success, negative error code on failure
 */
typedef int (*pwm_set_duty_fn_t)(void* handle, uint32_t channel, uint32_t duty);

/**
 * @brief PWM device instance structure (one per channel).
 *
 * All platform-specific operations are injected via function pointers.
 * The `handle` fields must be set by the user before any operation.
 */
typedef struct {
    void*            handle;   /**< Opaque peripheral handle (e.g. TIM_HandleTypeDef*) */
    uint32_t         channel;  /**< Channel identifier (e.g. TIM_CHANNEL_1) */
    pwm_start_fn_t   start;    /**< Function to start PWM */
    pwm_stop_fn_t    stop;     /**< Function to stop PWM */
    pwm_set_duty_fn_t set_duty; /**< Function to set duty cycle */
} PWM_Device_t;

/**
 * @brief Initialize a PWM device instance with the provided interface.
 *
 * @param dev     Pointer to a PWM_Device_t struct to initialize
 * @param handle  Opaque peripheral handle
 * @param channel Channel identifier
 * @param start   Function to start PWM on this channel
 * @param stop    Function to stop PWM on this channel
 * @param set_duty Function to set duty cycle on this channel
 * @return PWM_OK on success, PWM_ERR_NULL if dev is NULL or any function pointer is NULL
 */
int PWM_Device_Init(PWM_Device_t* dev,
                    void*         handle,
                    uint32_t      channel,
                    pwm_start_fn_t   start,
                    pwm_stop_fn_t    stop,
                    pwm_set_duty_fn_t set_duty);

/**
 * @brief Start PWM output on the device channel.
 *
 * Calls the injected start function.
 * @param dev Pointer to initialized PWM_Device_t
 * @return PWM_OK on success, negative error code on failure
 */
int PWM_Device_Start(PWM_Device_t* dev);

/**
 * @brief Stop PWM output on the device channel.
 *
 * Calls the injected stop function.
 * @param dev Pointer to initialized PWM_Device_t
 * @return PWM_OK on success, negative error code on failure
 */
int PWM_Device_Stop(PWM_Device_t* dev);

/**
 * @brief Set the duty cycle on the device channel.
 *
 * Calls the injected set_duty function.
 * @param dev  Pointer to initialized PWM_Device_t
 * @param duty Duty cycle value (0 to timer ARR for full range)
 * @return PWM_OK on success, PWM_ERR_PARAM if duty out of range (>999 if ARR=999), negative on other failures
 */
int PWM_Device_SetDuty(PWM_Device_t* dev, uint32_t duty);

/**
 * @brief De-initialize the device (clear fields).
 *        Does not call any hardware function.
 * @param dev Pointer to PWM_Device_t
 * @return PWM_OK on success, PWM_ERR_NULL if dev is NULL
 */
int PWM_Device_DeInit(PWM_Device_t* dev);

#endif /* PWM_DRIVER_H */
