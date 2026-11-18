// ふたまるの ESP32 ファームウェアの入り口（仕様 §9）
//
// 鉢の中の XIAO ESP32S3 がすること:
//   1. マイク・スピーカー（Atomic Voice Base）を使えるようにする
//   2. Wi-Fi につなぐ
//   3. LiveKit のルーム plant-<id> に入り、マイクの音を送りっぱなしにして、届いた声を鳴らす
// 考える処理（ウェイクワード・会話・記憶）はすべてサーバー側（agent/）にある。

#include <string.h>

#include "esp_log.h"
#include "esp_netif_sntp.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "livekit.h"
#include "livekit_example_utils.h"
#include "sdkconfig.h"

#include "board.h"
#include "media.h"
#include "room.h"
#include "status_led.h"
#include "token_info.h"

static const char *TAG = "futamaru";

#if CONFIG_FUTAMARU_USE_AEC
#define USE_AEC true
#else
#define USE_AEC false
#endif

/// 設定に足りないものがあれば、ログに出して false を返す
static bool check_config(void)
{
    bool ok = true;
    if (strlen(CONFIG_LK_EXAMPLE_WIFI_SSID) == 0) {
        ESP_LOGE(TAG, "Wi-Fi SSID is empty. Set it in sdkconfig.secrets");
        ok = false;
    }
    if (strlen(CONFIG_FUTAMARU_SERVER_URL) == 0) {
        ESP_LOGE(TAG, "LiveKit server URL is empty. Set it in sdkconfig.secrets");
        ok = false;
    }
    if (strlen(CONFIG_FUTAMARU_TOKEN) == 0) {
        ESP_LOGE(TAG, "LiveKit token is empty. Set it in sdkconfig.secrets");
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

    // 1. 設定の確認と、どの株として入るかの表示（2 台のトークンの取り違えに気づくため）
    if (!check_config()) {
        halt("missing settings (see firmware/README.md)");
    }
    char room_name[64];
    if (token_get_room(CONFIG_FUTAMARU_TOKEN, room_name, sizeof(room_name))) {
        ESP_LOGI(TAG, "This device joins room \"%s\"", room_name);
    } else {
        ESP_LOGW(TAG, "Could not read the room name from the token");
    }

    // 2. 音まわり（LiveKit SDK の初期化を先に行う。SDK のサンプルと同じ順番）
    livekit_system_init();
    if (!board_init(USE_AEC)) {
        halt("audio board not ready (check wiring)");
    }
    if (!media_init(USE_AEC)) {
        halt("media init failed");
    }

    // 3. 時刻合わせ（トークンの期限や TLS の確認に正しい時刻が要る）
    esp_sntp_config_t sntp_config =
        ESP_NETIF_SNTP_DEFAULT_CONFIG_MULTIPLE(2, ESP_SNTP_SERVER_LIST("time.google.com", "pool.ntp.org"));
    esp_netif_sntp_init(&sntp_config);

    // 4. Wi-Fi。sdkconfig.defaults で何度でも試す設定にしているので、つながるまでここで待つ
    ESP_LOGI(TAG, "Connecting to Wi-Fi \"%s\"", CONFIG_LK_EXAMPLE_WIFI_SSID);
    if (!lk_example_network_connect()) {
        halt("Wi-Fi connection failed");
    }

    // 5. LiveKit のルームへ（切れたら room.c のタスクが入り直す）
    status_led_set(STATUS_LED_ROOM);
    room_start();
}
