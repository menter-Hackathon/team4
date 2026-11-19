# ふたまるの ESP32 ファームウェア（XIAO ESP32S3 ＋ Atomic Voice Base、仕様 §9）
cmake_minimum_required(VERSION 3.16)

# Wi-Fi のパスワードと中継の合言葉（デバイスキー）は、git に入れない sdkconfig.secrets に書く。
# ファイルがあれば sdkconfig.defaults のあとに読み込む（後の方が優先）。
# コマンドラインで -D SDKCONFIG_DEFAULTS=... を渡したときは、そちらを使う。
if(NOT DEFINED SDKCONFIG_DEFAULTS AND EXISTS "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.secrets")
    set(SDKCONFIG_DEFAULTS "sdkconfig.defaults;sdkconfig.secrets")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(futamaru)
