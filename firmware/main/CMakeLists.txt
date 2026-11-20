# REQUIRES は書かない（ESP-IDF のサンプルと同じ）。書くと、使う部品（esp_websocket_client・esp_capture・
# json など）とそのヘッダが読む部品まで全部並べる必要があり、部品の版を上げたときに壊れやすいため
idf_component_register(
    SRCS "main.c" "board.c" "media.c" "relay.c" "mulaw.c" "status_led.c"
    INCLUDE_DIRS "."
)
