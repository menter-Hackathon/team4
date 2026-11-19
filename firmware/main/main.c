// ふたまるの ESP32 ファームウェアの入り口（仕様 §3.2.1・§9）
//
// 鉢の中の XIAO ESP32S3 がすること:
//   1. マイク・スピーカー（Atomic Voice Base）を使えるようにする
//   2. Wi-Fi につなぐ
//   3. サーバーの中継（relay）に WebSocket でつなぎ、マイクの音を送りっぱなしにして、届いた声を鳴らす
// 考える処理（ウェイクワード・会話・記憶）はすべてサーバー側（agent/）にある。
// LiveKit にはサーバー側の中継が入るので、この端末は LiveKit を知らなくてよい。

#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "livekit_example_utils.h"
#include "sdkconfig.h"

#include "board.h"
#include "media.h"
#include "relay.h"
#include "status_led.h"

static const char *TAG = "futamaru";

#if CONFIG_FUTAMARU_USE_AEC
#define USE_AEC true
#else
#define USE_AEC false
#endif

/// 設定に足りないもの・間違いがあれば、ログに出して false を返す
static bool check_config(void)
{
    bool ok = true;
    if (strlen(CONFIG_LK_EXAMPLE_WIFI_SSID) == 0) {
        ESP_LOGE(TAG, "Wi-Fi SSID is empty. Set it in sdkconfig.secrets");
        ok = false;
    }
    // URL は ws:// か wss:// で始まること（Cloudflare Tunnel 経由なら wss://<ホスト>/relay）
    const char *url = CONFIG_FUTAMARU_RELAY_URL;
    if (strlen(url) == 0) {
        ESP_LOGE(TAG, "Relay URL is empty. Set it in sdkconfig.secrets");
        ok = false;
    } else if (strncmp(url, "ws://", 5) != 0 && strncmp(url, "wss://", 6) != 0) {
        ESP_LOGE(TAG, "Relay URL must start with ws:// or wss:// (now: %s)", url);
        ok = false;
    }
    if (strlen(CONFIG_FUTAMARU_DEVICE_KEY) == 0) {
        ESP_LOGE(TAG, "Device key is empty. Set it in sdkconfig.secrets");
        ok = false;
    }
    if (!relay_plant_id_is_valid(CONFIG_FUTAMARU_PLANT_ID)) {
        ESP_LOGE(TAG, "Plant ID \"%s\" is invalid (use a-z, 0-9, _ and -, 1 to 32 chars)", CONFIG_FUTAMARU_PLANT_ID);
        ok = false;
    }
    return ok;
}

/// 直せない問題で止まるとき: LED でエラーを示し、ログを出し続ける（USB をつないだ人が気づけるように）
static void halt(const char *reason)
{
    status_led_set(STATUS_LED_ERROR);
    for (;;) {
        ESP_LOGE(TAG, "Stopped: %s", reason);
        vTaskDelay(pdMS_TO_TICKS(10000));
    }
}

void app_main(void)
{
    esp_log_level_set("*", ESP_LOG_INFO);
    status_led_init();
    status_led_set(STATUS_LED_NETWORK);

    // 1. 設定の確認と、どの株として話すかの表示（2 台の取り違えに気づくため）
    if (!check_config()) {
        halt("missing settings (see firmware/README.md)");
    }
    ESP_LOGI(TAG, "This device is plant \"%s\"", CONFIG_FUTAMARU_PLANT_ID);

    // 2. 音まわり（ボード → 録音・再生）
    if (!board_init(USE_AEC)) {
        halt("audio board not ready (check wiring)");
    }
    if (!media_init(USE_AEC)) {
        halt("media init failed");
    }
    // マイクの音は Wi-Fi を待たずに読み始める（つながるまでは捨てる。relay.h を参照）
    relay_start_mic();

    // 3. Wi-Fi。sdkconfig.defaults で何度でも試す設定にしているので、つながるまでここで待つ。
    //    つながったあとに切れても、livekit/example_utils がつなぎ直す
    ESP_LOGI(TAG, "Connecting to Wi-Fi \"%s\"", CONFIG_LK_EXAMPLE_WIFI_SSID);
    if (!lk_example_network_connect()) {
        halt("Wi-Fi connection failed");
    }

    // 4. 中継へ（切れたら relay.c のタスクがつなぎ直す）
    relay_start();
}
