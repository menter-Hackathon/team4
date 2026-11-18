# REQUIRES は書かない（SDK のサンプルと同じ）。書くと、SDK のヘッダが読む部品（esp_lcd など）まで
# 全部並べる必要があり、SDK の版を上げたときに壊れやすいため
idf_component_register(
    SRCS "main.c" "board.c" "media.c" "room.c" "status_led.c" "token_info.c"
    INCLUDE_DIRS "."
)
