#include "driver/i2c_master.h"

/* SHT30 temperature and humidity sensor on I2C_NUM_0. */
static const int sensor_bus = I2C_NUM_0;
static const int device_address = 0x44;

int sht30_read(void) {
    (void)sensor_bus;
    (void)device_address;
    /* i2c_master_bus_add_device / i2c_master_transmit */
    return 0;
}
