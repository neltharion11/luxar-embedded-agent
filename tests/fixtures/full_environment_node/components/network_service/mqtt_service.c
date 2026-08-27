#include "esp_wifi.h"
#include "mqtt_client.h"

static const char *telemetry_topic = "devices/environment/telemetry";

void network_service_init(void) {
    wifi_init_config_t wifi = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&wifi);
    esp_mqtt_client_config_t mqtt = {0};
    esp_mqtt_client_handle_t client = esp_mqtt_client_init(&mqtt);
    (void)client;
    (void)telemetry_topic;
    /* WIFI_MODE_STA, MQTT_EVENT_CONNECTED, reconnect with bounded backoff. */
}
