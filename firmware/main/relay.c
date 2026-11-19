// サーバーの中継（relay）と WebSocket でつながり、音をやり取りする（仕様 §3.2.1）
//
// 決まり（仕様 §3.2.1。サーバー側と同じ）:
//   - つなぎ先: CONFIG_FUTAMARU_RELAY_URL（wss://… または手元の試験用の ws://…）
//   - HTTP ヘッダ「Authorization: Bearer <CONFIG_FUTAMARU_DEVICE_KEY>」を付ける
//   - 音: 16kHz・16 ビット・モノラルの PCM。1 フレーム = 20ms = 640 バイト。送りも受けも同じ
//
// 手順（run_session()）:
//   1. つながったら 5 秒以内に、文字で {"type":"start","version":1,"plant_id":"a1"} を送る
//   2. サーバーから {"type":"ready","sample_rate":16000,"frame_bytes":640} が来るのを待つ
//   3. ready のあとは、マイクの音を 640 バイトずつバイナリで送り続ける（mic_task）
//   4. サーバーから届くバイナリ（植物の声）は、届いた順に再生の待ち行列へ入れる（media_play）。
//      無音のフレームは送られてこないので、待ち行列が空のあいだは media.c が無音を鳴らす
//
// エラー: サーバーは {"type":"error","code":"…","message":"…"} を送ってから接続を閉じる。ログに出す。
// 生きているかの確認: サーバーは 15 秒ごとに ping を送る（pong は esp_websocket_client が自動で返す）。
//   サーバーは端末から 30 秒何も届かないと閉じるが、マイクの音を送り続けるので普段は起きない。
// 切れたら: 5 秒待ってつなぎ直す。続けて失敗するたびに待ち時間を倍にする（最長 60 秒）。
//   ready まで進めたら、待ち時間を 5 秒に戻す。

#include "relay.h"

#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_log.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#include "media.h"
#include "status_led.h"

static const char *TAG = "relay";

// 待ち時間など
#define CONNECT_TIMEOUT_MS 20000 // WebSocket がつながるまで待つ最長の時間
#define READY_TIMEOUT_MS 10000   // start を送ってから ready を待つ最長の時間
#define SEND_TIMEOUT_MS 1000     // 1 回の送信で待つ最長の時間
#define BACKOFF_FIRST_MS 5000    // つなぎ直す前の最初の待ち時間
#define BACKOFF_MAX_MS 60000     // つなぎ直す前の最長の待ち時間
#define STATS_INTERVAL_MS 30000  // 送り受けした量をログに出す間隔

// WebSocket のフレームの種類（RFC 6455 の opcode）
#define WS_OP_CONTINUATION 0x0
#define WS_OP_TEXT 0x1
#define WS_OP_BINARY 0x2
#define WS_OP_CLOSE 0x8

// WebSocket の受信の処理（別のタスク）から relay_task に知らせる合図
#define EV_CONNECTED BIT0 // WebSocket がつながった
#define EV_READY BIT1     // ready が届いた
#define EV_ENDED BIT2     // 切れた・閉じた（もう使えない）

static EventGroupHandle_t s_events;

// 今の接続と「マイクの音を送ってよいか」。relay_task と mic_task の両方が触るので、s_lock で守る
static SemaphoreHandle_t s_lock;
static esp_websocket_client_handle_t s_client;
static bool s_streaming;

// 送り受けした量（ログ用）
static volatile uint32_t s_mic_frames_sent;
static volatile uint32_t s_voice_bytes_received;

// 受信の処理だけが使う変数（1 つのタスクからしか触らない）
static char s_text[512];       // 文字のメッセージを組み立てる場所
static size_t s_text_len;      // s_text にたまった長さ
static bool s_text_overflow;   // s_text に入りきらなかった
static uint8_t s_data_opcode;  // 続きのフレーム（opcode 0）が文字か音かを覚えておく

// 「Authorization: Bearer <鍵>\r\n」を入れる場所。大きさは鍵の長さから決まる
static char s_headers[sizeof("Authorization: Bearer \r\n") + sizeof(CONFIG_FUTAMARU_DEVICE_KEY)];

// ---------------------------------------------------------------------------
// plant_id の確認
// ---------------------------------------------------------------------------

bool relay_plant_id_is_valid(const char *plant_id)
{
    size_t len = strlen(plant_id);
    if (len < 1 || len > 32) {
        return false;
    }
    for (size_t i = 0; i < len; i++) {
        char c = plant_id[i];
        bool ok = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_' || c == '-';
        if (!ok) {
            return false;
        }
    }
    return true;
}

// ---------------------------------------------------------------------------
// サーバーから届いた文字のメッセージ（JSON）
// ---------------------------------------------------------------------------

/// JSON の中の文字列を取り出す。無ければ "?" を返す
static const char *json_string(const cJSON *obj, const char *key)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(obj, key);
    return cJSON_IsString(item) ? item->valuestring : "?";
}

/// 届いた文字のメッセージを読んで、ready なら relay_task に知らせ、error ならログに出す。
///
/// 例: {"type":"ready","sample_rate":16000,"frame_bytes":640} → EV_READY を立てる
///     {"type":"error","code":"invalid_start","message":"..."} → エラーのログ
static void handle_text_message(const char *text, size_t len)
{
    cJSON *msg = cJSON_ParseWithLength(text, len);
    if (msg == NULL) {
        ESP_LOGW(TAG, "Received text that is not JSON (%d bytes)", (int)len);
        return;
    }
    const char *type = json_string(msg, "type");
    if (strcmp(type, "ready") == 0) {
        // 音の形式がこちらの決め打ち（16kHz・640 バイト）と違えば、警告だけ出して続ける
        const cJSON *rate = cJSON_GetObjectItemCaseSensitive(msg, "sample_rate");
        const cJSON *frame = cJSON_GetObjectItemCaseSensitive(msg, "frame_bytes");
        if (!cJSON_IsNumber(rate) || rate->valueint != MEDIA_SAMPLE_RATE || !cJSON_IsNumber(frame) ||
            frame->valueint != MEDIA_FRAME_BYTES) {
            ESP_LOGW(TAG, "Relay expects a different audio format (sample_rate=%d, frame_bytes=%d)",
                     cJSON_IsNumber(rate) ? rate->valueint : -1, cJSON_IsNumber(frame) ? frame->valueint : -1);
        }
        xEventGroupSetBits(s_events, EV_READY);
    } else if (strcmp(type, "error") == 0) {
        // invalid_start: plant_id などの間違い / invalid_audio: 音の形式の間違い / livekit_unavailable: サーバー側の不調
        ESP_LOGE(TAG, "Relay error: code=%s message=%s", json_string(msg, "code"), json_string(msg, "message"));
    } else {
        ESP_LOGW(TAG, "Unknown message type \"%s\"", type);
    }
    cJSON_Delete(msg);
}

// ---------------------------------------------------------------------------
// WebSocket の出来事（esp_websocket_client のタスクから呼ばれる）
// ---------------------------------------------------------------------------

/// 届いたデータを、文字なら組み立てて読み、音なら再生の待ち行列へ入れる。
///
/// 大きなメッセージは何回かに分けて届く（payload_offset が進む）ので、文字は最後まで集めてから読む。
static void handle_data(const esp_websocket_event_data_t *d)
{
    // 1. フレームの種類を決める。続きのフレーム（opcode 0）は、直前の文字・音の続き
    uint8_t op = d->op_code;
    bool new_message = op != WS_OP_CONTINUATION && d->payload_offset == 0;
    if (op == WS_OP_TEXT || op == WS_OP_BINARY) {
        s_data_opcode = op;
    } else if (op == WS_OP_CONTINUATION) {
        op = s_data_opcode;
    }

    switch (op) {
    case WS_OP_BINARY:
        // 2. 植物の声: 届いた順にそのまま再生へ
        if (d->data_len > 0) {
            media_play((const uint8_t *)d->data_ptr, (size_t)d->data_len);
            s_voice_bytes_received += (uint32_t)d->data_len;
        }
        break;

    case WS_OP_TEXT:
        // 3. 文字: 最後の一切れまで集めてから JSON として読む
        if (new_message) {
            s_text_len = 0;
            s_text_overflow = false;
        }
        if (s_text_len + (size_t)d->data_len < sizeof(s_text)) {
            memcpy(s_text + s_text_len, d->data_ptr, d->data_len);
            s_text_len += (size_t)d->data_len;
        } else {
            s_text_overflow = true;
        }
        if (d->fin && d->payload_offset + d->data_len >= d->payload_len) {
            if (s_text_overflow) {
                ESP_LOGW(TAG, "Received text message is too long; ignored");
            } else {
                handle_text_message(s_text, s_text_len);
            }
            s_text_len = 0;
            s_text_overflow = false;
        }
        break;

    case WS_OP_CLOSE:
        // 4. サーバーが閉じた。中身の先頭 2 バイトが理由の番号（例: 1008 = 決まり違反、1013 = あとで試して）
        if (d->data_len >= 2) {
            int code = ((uint8_t)d->data_ptr[0] << 8) | (uint8_t)d->data_ptr[1];
            ESP_LOGW(TAG, "Relay closed the connection (code %d: %.*s)", code, d->data_len - 2, d->data_ptr + 2);
        } else {
            ESP_LOGW(TAG, "Relay closed the connection");
        }
        break;

    default:
        // ping・pong など。esp_websocket_client が自動で扱うので何もしない
        break;
    }
}

static void ws_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data)
{
    const esp_websocket_event_data_t *d = (const esp_websocket_event_data_t *)event_data;
    switch (event_id) {
    case WEBSOCKET_EVENT_CONNECTED:
        ESP_LOGI(TAG, "WebSocket connected");
        xEventGroupSetBits(s_events, EV_CONNECTED);
        break;
    case WEBSOCKET_EVENT_DATA:
        handle_data(d);
        break;
    case WEBSOCKET_EVENT_ERROR:
        // 原因を絞るための情報。HTTP 401/403 なら鍵の間違い、404 なら URL の道（/relay）の間違い
        ESP_LOGE(TAG, "WebSocket error (HTTP status %d, TLS error 0x%x, socket errno %d)",
                 d->error_handle.esp_ws_handshake_status_code, d->error_handle.esp_tls_last_esp_err,
                 d->error_handle.esp_transport_sock_errno);
        break;
    case WEBSOCKET_EVENT_DISCONNECTED:
    case WEBSOCKET_EVENT_CLOSED:
    case WEBSOCKET_EVENT_FINISH:
        xEventGroupSetBits(s_events, EV_ENDED);
        break;
    default:
        break;
    }
}

// ---------------------------------------------------------------------------
// マイクの音を送るタスク
//
// マイクの音は、つながっていないあいだも読み続けて捨てる（録音の仕組みの中に音がたまり続けないように）。
// ready のあとだけ、640 バイトずつバイナリで送る。
// ---------------------------------------------------------------------------

static void mic_task(void *arg)
{
    static uint8_t frame[MEDIA_FRAME_BYTES];
    uint32_t send_failures = 0;

    for (;;) {
        // 1. 20ms 分のマイクの音を待つ
        if (!media_read_mic_frame(frame)) {
            ESP_LOGE(TAG, "Failed to read the microphone");
            vTaskDelay(pdMS_TO_TICKS(100));
            continue;
        }
        // 2. ready のあとなら送る（接続を片付けている最中に送らないよう、s_lock を持って送る）
        xSemaphoreTake(s_lock, portMAX_DELAY);
        if (s_client != NULL && s_streaming) {
            int sent = esp_websocket_client_send_bin(s_client, (const char *)frame, MEDIA_FRAME_BYTES,
                                                     pdMS_TO_TICKS(SEND_TIMEOUT_MS));
            if (sent < 0) {
                // ログが流れすぎないよう、50 回に 1 回だけ出す
                if (send_failures++ % 50 == 0) {
                    ESP_LOGW(TAG, "Failed to send mic audio (%lu times)", (unsigned long)send_failures);
                }
            } else {
                s_mic_frames_sent++;
            }
        }
        xSemaphoreGive(s_lock);
    }
}

// ---------------------------------------------------------------------------
// 1 回分の接続（つなぐ → start → ready → 切れるまで待つ → 片付け）
// ---------------------------------------------------------------------------

/// 中継に 1 回つなぎ、切れるまで待つ。
///
/// 戻り値: ready まで進めたら true（つなぎ直しの待ち時間を 5 秒に戻すため）
static bool run_session(void)
{
    bool got_ready = false;
    status_led_set(STATUS_LED_RELAY);
    xEventGroupClearBits(s_events, EV_CONNECTED | EV_READY | EV_ENDED);

    // 1. WebSocket の準備。つなぎ直しはこのファイルで行うので、esp_websocket_client の自動の再接続は切る
    esp_websocket_client_config_t cfg = {
        .uri = CONFIG_FUTAMARU_RELAY_URL,
        .headers = s_headers,
        .crt_bundle_attach = esp_crt_bundle_attach, // wss:// のとき、ESP-IDF 同梱の証明書でサーバーを確かめる
        .disable_auto_reconnect = true,
        .network_timeout_ms = 10000,
        .pingpong_timeout_sec = 45, // サーバーが黙って消えたとき（Wi-Fi 切れなど）に気づくため
        .buffer_size = 2048,        // 640 バイトの音が 1 回で届く大きさ
        .task_stack = 6144,
        .task_prio = 6,
    };
    esp_websocket_client_handle_t client = esp_websocket_client_init(&cfg);
    if (client == NULL) {
        ESP_LOGE(TAG, "Failed to create the WebSocket client");
        return false;
    }
    esp_websocket_register_events(client, WEBSOCKET_EVENT_ANY, ws_event_handler, NULL);
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_client = client;
    s_streaming = false;
    xSemaphoreGive(s_lock);

    // 2. つなぐ
    ESP_LOGI(TAG, "Connecting to %s", CONFIG_FUTAMARU_RELAY_URL);
    if (esp_websocket_client_start(client) != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start the WebSocket client");
        goto cleanup;
    }
    EventBits_t bits = xEventGroupWaitBits(s_events, EV_CONNECTED | EV_ENDED, pdFALSE, pdFALSE,
                                           pdMS_TO_TICKS(CONNECT_TIMEOUT_MS));
    if (!(bits & EV_CONNECTED) || (bits & EV_ENDED)) {
        ESP_LOGW(TAG, "Could not connect to the relay");
        goto cleanup;
    }

    // 3. start を送る（つながってから 5 秒以内に送る決まり）
    char start_msg[96];
    int len = snprintf(start_msg, sizeof(start_msg), "{\"type\":\"start\",\"version\":1,\"plant_id\":\"%s\"}",
                       CONFIG_FUTAMARU_PLANT_ID);
    if (esp_websocket_client_send_text(client, start_msg, len, pdMS_TO_TICKS(SEND_TIMEOUT_MS)) < 0) {
        ESP_LOGW(TAG, "Failed to send start");
        goto cleanup;
    }
    ESP_LOGI(TAG, "Sent start (plant_id \"%s\"), waiting for ready", CONFIG_FUTAMARU_PLANT_ID);

    // 4. ready を待つ
    bits = xEventGroupWaitBits(s_events, EV_READY | EV_ENDED, pdFALSE, pdFALSE, pdMS_TO_TICKS(READY_TIMEOUT_MS));
    if (!(bits & EV_READY) || (bits & EV_ENDED)) {
        ESP_LOGW(TAG, "Relay did not send ready");
        goto cleanup;
    }
    got_ready = true;
    ESP_LOGI(TAG, "Relay is ready. Streaming the microphone");
    status_led_set(STATUS_LED_READY);
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_streaming = true;
    xSemaphoreGive(s_lock);

    // 5. 切れるまで待つ。合図を取りこぼしたときのために、1 秒ごとに「まだつながっているか」も見る
    int not_connected_count = 0;
    TickType_t last_stats = xTaskGetTickCount();
    for (;;) {
        bits = xEventGroupWaitBits(s_events, EV_ENDED, pdFALSE, pdFALSE, pdMS_TO_TICKS(1000));
        if (bits & EV_ENDED) {
            break;
        }
        not_connected_count = esp_websocket_client_is_connected(client) ? 0 : not_connected_count + 1;
        if (not_connected_count >= 3) {
            break;
        }
        if (xTaskGetTickCount() - last_stats >= pdMS_TO_TICKS(STATS_INTERVAL_MS)) {
            last_stats = xTaskGetTickCount();
            ESP_LOGI(TAG, "Mic frames sent: %lu, voice received: %lu bytes", (unsigned long)s_mic_frames_sent,
                     (unsigned long)s_voice_bytes_received);
        }
    }
    ESP_LOGW(TAG, "Disconnected from the relay");

cleanup:
    // 6. 片付け。先に mic_task が送らないようにしてから、WebSocket を止めて消す
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_streaming = false;
    s_client = NULL;
    xSemaphoreGive(s_lock);
    esp_websocket_client_destroy(client);
    return got_ready;
}

// ---------------------------------------------------------------------------
// つなぎ直しを受け持つタスク
// ---------------------------------------------------------------------------

static void relay_task(void *arg)
{
    int backoff_ms = BACKOFF_FIRST_MS;
    for (;;) {
        bool got_ready = run_session();
        // ready まで進めたら、待ち時間を最初の 5 秒に戻す
        if (got_ready) {
            backoff_ms = BACKOFF_FIRST_MS;
        }
        status_led_set(STATUS_LED_RELAY);
        ESP_LOGI(TAG, "Reconnecting in %d s", backoff_ms / 1000);
        vTaskDelay(pdMS_TO_TICKS(backoff_ms));
        // 次も失敗したら、待ち時間を倍にする（5 → 10 → 20 → 40 → 60 秒）
        backoff_ms = backoff_ms * 2 > BACKOFF_MAX_MS ? BACKOFF_MAX_MS : backoff_ms * 2;
    }
}

void relay_start(void)
{
    // 1. タスクのあいだで使う合図・鍵・ヘッダを用意する
    s_events = xEventGroupCreate();
    s_lock = xSemaphoreCreateMutex();
    snprintf(s_headers, sizeof(s_headers), "Authorization: Bearer %s\r\n", CONFIG_FUTAMARU_DEVICE_KEY);

    // 2. マイクの音を送るタスクと、接続を受け持つタスクを始める
    xTaskCreate(mic_task, "relay_mic", 4096, NULL, 8, NULL);
    xTaskCreate(relay_task, "relay", 6144, NULL, 5, NULL);
}
