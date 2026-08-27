#include "nvs.h"

void config_store_open(void) {
    nvs_handle_t handle;
    nvs_open("device_config", NVS_READWRITE, &handle);
}
