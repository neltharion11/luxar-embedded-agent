#include "driver/gpio.h"
#include "diagnostics.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

static QueueHandle_t data_queue;

static void acquisition_task(void *argument) { (void)argument; for (;;) { diagnostics_report(); vTaskDelay(pdMS_TO_TICKS(1000)); } }
static void display_task(void *argument) { (void)argument; for (;;) { vTaskDelay(pdMS_TO_TICKS(1000)); } }
static void upload_task(void *argument) { (void)argument; for (;;) { vTaskDelay(pdMS_TO_TICKS(1000)); } }
static void command_task(void *argument) { (void)argument; for (;;) { vTaskDelay(pdMS_TO_TICKS(1000)); } }

void app_main(void) {
    gpio_config_t status_led = {0};
    status_led.mode = GPIO_MODE_OUTPUT;
    status_led.pin_bit_mask = 1ULL << GPIO_NUM_13;
    gpio_config(&status_led);
    gpio_set_level(GPIO_NUM_13, 1);

    data_queue = xQueueCreate(8, sizeof(int));
    xTaskCreate(acquisition_task, "acquisition", 4096, NULL, 5, NULL);
    xTaskCreate(display_task, "display", 4096, NULL, 4, NULL);
    xTaskCreate(upload_task, "upload", 6144, NULL, 4, NULL);
    xTaskCreate(command_task, "command", 4096, NULL, 4, NULL);
}
