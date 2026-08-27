from luxar.domain.agent.project_inspector import ProjectModelExtractor
from luxar.domain.repairs import ProjectFile


def full_environment_files() -> list[ProjectFile]:
    return [
        ProjectFile(
            path="CMakeLists.txt",
            content="""
cmake_minimum_required(VERSION 3.22)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(environment_node)
""",
        ),
        ProjectFile(
            path="sdkconfig.defaults",
            content='CONFIG_IDF_TARGET="esp32"\nCONFIG_ESPTOOLPY_FLASHSIZE_4MB=y\n',
        ),
        ProjectFile(
            path="partitions.csv",
            content="""
# Name, Type, SubType, Offset, Size
nvs,data,nvs,0x9000,0x6000
otadata,data,ota,0xf000,0x2000
app0,app,ota_0,0x20000,1M
app1,app,ota_1,,1M
""",
        ),
        ProjectFile(
            path="main/CMakeLists.txt",
            content='idf_component_register(SRCS "main.c" INCLUDE_DIRS "." REQUIRES sensor mqtt)',
        ),
        ProjectFile(
            path="main/main.c",
            content="""
#include "driver/i2c_master.h"
#include "mqtt_client.h"
#include "nvs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

static void acquisition_task(void *arg) {
    xQueueSend(data_queue, &reading, 0);
    i2c_master_transmit(sensor, data, 2, 100);
}

void app_main(void) {
    gpio_config_t config = {};
    config.mode = GPIO_MODE_OUTPUT;
    config.pin_bit_mask = 1ULL << GPIO_NUM_13;
    gpio_config(&config);
    xQueueCreate(8, sizeof(int));
    xTaskCreate(acquisition_task, "acquisition", 4096, NULL, 5, NULL);
    nvs_open("config", NVS_READWRITE, &handle);
    esp_mqtt_client_init(&mqtt_config);
}
""",
        ),
        ProjectFile(
            path="components/sensor/CMakeLists.txt",
            content='idf_component_register(SRCS "sht30.c" INCLUDE_DIRS "." REQUIRES driver)',
        ),
        ProjectFile(
            path="components/sensor/sht30.c",
            content="""
#include "driver/i2c_master.h"
// SHT30 temperature and humidity sensor
int sht30_read(void) { return 0; }
""",
        ),
        ProjectFile(
            path="components/display/CMakeLists.txt",
            content='idf_component_register(SRCS "oled.c" INCLUDE_DIRS "." REQUIRES driver)',
        ),
        ProjectFile(
            path="components/display/oled.c",
            content="// SSD1306 OLED display\nvoid display_frame(void) {}\n",
        ),
        ProjectFile(
            path="main/Kconfig.projbuild",
            content='menu "Environment"\nconfig DEVICE_NAME\n    string "Name"\nendmenu\n',
        ),
        ProjectFile(
            path="main/idf_component.yml",
            content="dependencies:\n  espressif/mqtt: ^1.0.0\n",
        ),
    ]


def test_static_project_model_extracts_components_resources_and_dataflow() -> None:
    model = ProjectModelExtractor().extract(
        full_environment_files(),
        project_name="environment_node",
    )

    assert model.target_chip == "esp32"
    assert model.configuration.partition_entries[0]["name"] == "nvs"
    assert "main" in {item.component_id for item in model.component_graph.components}
    assert "sensor" in {item.component_id for item in model.component_graph.components}
    main_component = next(
        item for item in model.component_graph.components if item.component_id == "main"
    )
    assert main_component.dependencies == ["sensor", "mqtt"]
    assert "bus.i2c" in {item.capability_id for item in model.capabilities}
    assert "network.mqtt_client" in {item.capability_id for item in model.capabilities}
    assert "task.freertos:acquisition_task" in {
        item.capability_id for item in model.capabilities
    }
    assert "gpio:P13" in {
        item.resource_id for item in model.resource_graph.allocations
    }
    flow = model.data_flows[0]
    assert {item.node_id for item in flow.nodes} >= {
        "sensor.sht30",
        "bus.i2c",
        "task:acquisition_task",
        "queue:data",
        "network:mqtt",
    }
    assert all(node.evidence_ids for node in flow.nodes)
    assert all(fact.source_kind == "source" for fact in model.facts)


def test_static_model_detects_same_gpio_in_different_components() -> None:
    files = [
        ProjectFile(
            path="main/main.c",
            content="""
#include "driver/gpio.h"
void app_main(void) {
    gpio_config_t c = {};
    c.mode = GPIO_MODE_OUTPUT;
    c.pin_bit_mask = 1ULL << GPIO_NUM_13;
}
""",
        ),
        ProjectFile(
            path="components/other/other.c",
            content="""
#include "driver/gpio.h"
void other(void) {
    gpio_config_t c = {};
    c.mode = GPIO_MODE_OUTPUT;
    c.pin_bit_mask = 1ULL << GPIO_NUM_13;
}
""",
        ),
    ]

    model = ProjectModelExtractor().extract(files)

    assert model.resource_graph.has_blocking_conflict is True
    assert model.blocking_conflicts[0].resource_ids == ["gpio:P13"]


def test_static_model_returns_safe_empty_baseline_without_model_service() -> None:
    model = ProjectModelExtractor().extract([])

    assert model.project_exists is True
    assert model.component_graph.components == []
    assert model.capabilities == []
    assert model.resource_graph.conflicts == []
    assert model.warnings
    assert model.fingerprint
