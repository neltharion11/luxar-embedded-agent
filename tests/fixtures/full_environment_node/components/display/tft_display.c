#include "driver/gpio.h"
#include "driver/spi_master.h"

/* SPI2_HOST display using an independent CS pin. */
static const int display_cs = GPIO_NUM_5;

void display_init(void) {
    spi_device_interface_config_t config = { .spics_io_num = GPIO_NUM_5 };
    (void)display_cs;
    (void)config;
    /* spi_bus_initialize(SPI2_HOST, ...); spi_bus_add_device(SPI2_HOST, ...); */
}
