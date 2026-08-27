idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES env_sensor display modbus_service network_service storage_service ota_service diagnostics
)
