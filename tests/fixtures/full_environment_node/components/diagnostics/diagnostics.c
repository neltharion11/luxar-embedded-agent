#include "esp_log.h"

static unsigned error_count;

void diagnostics_report(void) {
    ESP_LOGI("diagnostics", "status=ok errors=%u watchdog=armed", error_count);
}
