// LiveKit のルーム plant-<id> に入り、切れたら入り直す（仕様 §9 の 1〜3）
//
// - マイクの音は入っている間ずっと送る（ウェイクワードはサーバー側で聞き取るため。仕様 §9 の 2）
// - エージェントの声を受け取ってスピーカーで鳴らす（仕様 §9 の 3）
// - 入るルームはトークンで決まる（scripts/issue_token.py が plant-<id> 用に発行する）
//
// 入り直しの流れ:
//   SDK は一時的な切断なら自分でつなぎ直す（RECONNECTING）。
//   それでもだめだと FAILED になるので、このファイルのタスクがルームを作り直して入り直す。
//   SDK のコールバックの中でルームを壊すと危ないので、コールバックは「入り直して」の印を立てるだけにして、
//   実際の作り直しは専用のタスク（room_task）で行う。

#include "room.h"

#include <stdbool.h>
#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "livekit.h"
#include "sdkconfig.h"

#include "media.h"
#include "status_led.h"

static const char *TAG = "room";

// 入り直すまでの待ち時間。失敗が続くたびに倍にして、サーバーに負担をかけないようにする
#define RETRY_DELAY_FIRST_MS 5000
#define RETRY_DELAY_MAX_MS 60000

#define EV_REJOIN BIT0 // 「ルームを作り直して入り直して」の印

static EventGroupHandle_t s_events;
static livekit_room_handle_t s_room;
// true の間に DISCONNECTED になったら、自分で閉じたのではない（サーバーに切られた）とみなす
static volatile bool s_session_active;
static volatile bool s_connected_once;
static volatile bool s_agent_present;

static void update_led(livekit_connection_state_t state)
{
    switch (state) {
    case LIVEKIT_CONNECTION_STATE_CONNECTED:
        status_led_set(s_agent_present ? STATUS_LED_READY : STATUS_LED_WAIT_AGENT);
        break;
    case LIVEKIT_CONNECTION_STATE_FAILED:
        status_led_set(STATUS_LED_ERROR);
        break;
    default:
        status_led_set(STATUS_LED_ROOM);
        break;
    }
}

/// SDK から呼ばれる: 接続の状態が変わった
static void on_state_changed(livekit_connection_state_t state, void *ctx)
{
    ESP_LOGI(TAG, "Room state: %s", livekit_connection_state_str(state));
    update_led(state);

    switch (state) {
    case LIVEKIT_CONNECTION_STATE_CONNECTED:
        s_connected_once = true;
        break;
    case LIVEKIT_CONNECTION_STATE_FAILED: {
        livekit_failure_reason_t reason = livekit_room_get_failure_reason(s_room);
        ESP_LOGE(TAG, "Connection failed: %s", livekit_failure_reason_str(reason));
        if (reason == LIVEKIT_FAILURE_REASON_BAD_TOKEN || reason == LIVEKIT_FAILURE_REASON_UNAUTHORIZED) {
            // トークンの期限切れ・取り違えなど。入り直しても直らないので、書き込み直しを促す
            ESP_LOGE(TAG, "Token was rejected. Issue a new one with scripts/issue_token.py and flash again");
        }
        xEventGroupSetBits(s_events, EV_REJOIN);
        break;
    }
    case LIVEKIT_CONNECTION_STATE_DISCONNECTED:
        if (s_session_active) {
            // ルームが消された・サーバーから外されたなど。入り直す
            ESP_LOGW(TAG, "Disconnected by server; will rejoin");
            xEventGroupSetBits(s_events, EV_REJOIN);
        }
        break;
    default:
        break;
    }
}

/// SDK から呼ばれる: ルームの参加者の情報が届いた（エージェントが来た・去ったを LED に出す）
static void on_participant_info(const livekit_participant_info_t *info, void *ctx)
{
    if (info->kind != LIVEKIT_PARTICIPANT_KIND_AGENT) {
        return;
    }
    bool present;
    switch (info->state) {
    case LIVEKIT_PARTICIPANT_STATE_ACTIVE:
        present = true;
        break;
    case LIVEKIT_PARTICIPANT_STATE_DISCONNECTED:
        present = false;
        break;
    default:
        return;
    }
    if (present != s_agent_present) {
        ESP_LOGI(TAG, "Agent %s the room", present ? "joined" : "left");
        s_agent_present = present;
        update_led(livekit_room_get_state(s_room));
    }
}

/// ルームを作って接続を始める。戻り値: 接続を始められたら true
static bool join(void)
{
    livekit_room_options_t options = {
        .publish = {
            .kind = LIVEKIT_MEDIA_TYPE_AUDIO,
            .audio_encode = {
                .codec = LIVEKIT_AUDIO_CODEC_OPUS,
                .sample_rate = 16000,
                .channel_count = 1,
            },
            .capturer = media_get_capturer(),
        },
        .subscribe = {
            .kind = LIVEKIT_MEDIA_TYPE_AUDIO,
            .renderer = media_get_renderer(),
        },
        .on_state_changed = on_state_changed,
        .on_participant_info = on_participant_info,
    };
    if (livekit_room_create(&s_room, &options) != LIVEKIT_ERR_NONE) {
        ESP_LOGE(TAG, "Failed to create room");
        s_room = NULL;
        return false;
    }
    s_session_active = true;
    if (livekit_room_connect(s_room, CONFIG_FUTAMARU_SERVER_URL, CONFIG_FUTAMARU_TOKEN) != LIVEKIT_ERR_NONE) {
        ESP_LOGE(TAG, "Failed to start connecting");
        return false;
    }
    return true;
}

/// ルームから出て、ルームを壊す
static void leave(void)
{
    s_session_active = false; // これから出る DISCONNECTED は自分で閉じたもの
    s_agent_present = false;
    if (s_room == NULL) {
        return;
    }
    livekit_room_close(s_room);
    livekit_room_destroy(s_room);
    s_room = NULL;
}

static void room_task(void *arg)
{
    int delay_ms = RETRY_DELAY_FIRST_MS;
    for (;;) {
        // 1. 入る
        xEventGroupClearBits(s_events, EV_REJOIN);
        s_connected_once = false;
        status_led_set(STATUS_LED_ROOM);
        bool started = join();

        // 2. 失敗・切断の知らせが来るまで待つ（つながっている間はここで止まっている）
        if (started) {
            xEventGroupWaitBits(s_events, EV_REJOIN, pdTRUE, pdFALSE, portMAX_DELAY);
        }

        // 3. 出て、少し待ってから入り直す。一度でもつながったなら待ち時間を最初に戻す
        if (s_connected_once) {
            delay_ms = RETRY_DELAY_FIRST_MS;
        }
        leave();
        ESP_LOGI(TAG, "Rejoining in %d s", delay_ms / 1000);
        vTaskDelay(pdMS_TO_TICKS(delay_ms));
        delay_ms = delay_ms * 2 > RETRY_DELAY_MAX_MS ? RETRY_DELAY_MAX_MS : delay_ms * 2;
    }
}

void room_start(void)
{
    s_events = xEventGroupCreate();
    xTaskCreate(room_task, "room", 6144, NULL, 5, NULL);
}
