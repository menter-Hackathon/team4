idf_component_register(
    SRCS "main.c" "board.c" "media.c" "room.c" "status_led.c" "token_info.c"
    INCLUDE_DIRS "."
    REQUIRES esp_driver_i2c esp_driver_i2s esp_driver_gpio esp_netif json mbedtls
)
