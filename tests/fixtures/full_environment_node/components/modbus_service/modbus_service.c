#include "driver/uart.h"

/* Modbus RTU over RS485 uses UART_NUM_2, leaving the console UART untouched. */
void modbus_service_init(void) {
    uart_driver_install(UART_NUM_2, 1024, 1024, 8, NULL, 0);
    /* mbc_master_init(MB_PORT_SERIAL_MASTER, ...); MODBUS_RTU */
}
