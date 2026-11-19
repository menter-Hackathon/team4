// XIAO の基板上の LED（GPIO21）で、今どこまでつながっているかを見せる
//
// 鉢に入れるとシリアルのログが見られないので、LED で大まかな状態がわかるようにする。
// XIAO ESP32S3 の LED は「Low で点灯」（Seeed の資料）。逆に光るときは LED_ON_LEVEL を 1 にする。

#include "status_led.h"

#include <stdbool.h>

#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"

#define LED_PIN GPIO_NUM_21
#define LED_ON_LEVEL 0

static volatile status_led_mode_t s_mode = STATUS_LED_OFF;

/// 今の光り方で、時刻 tick_ms（0〜1999 を繰り返す）に点けるかどうか
static bool led_is_on(status_led_mode_t mode, int tick_ms)
{
    switch (mode) {
    case STATUS_LED_READY:
        return true;
    case STATUS_LED_NETWORK: // 200ms ごとに点滅
        return (tick_ms / 100) % 2 == 0;
    case STATUS_LED_RELAY: // 1 秒ごとに点滅
        return (tick_ms / 500) % 2 == 0;
    case STATUS_LED_ERROR: // 2 秒に 2 回、短く光る
        return tick_ms < 100 || (tick_ms >= 300 && tick_ms < 400);
    case STATUS_LED_OFF:
    default:
        return false;
    }
}

static void led_task(void *arg)
{
    int tick_ms = 0;
    for (;;) {
        bool on = led_is_on(s_mode, tick_ms);
        gpio_set_level(LED_PIN, on ? LED_ON_LEVEL : !LED_ON_LEVEL);
        vTaskDelay(pdMS_TO_TICKS(50));
        tick_ms = (tick_ms + 50) % 2000;
    }
}

void status_led_init(void)
{
#if CONFIG_FUTAMARU_STATUS_LED
    gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << LED_PIN,
        .mode = GPIO_MODE_OUTPUT,
    };
    gpio_config(&cfg);
    gpio_set_level(LED_PIN, !LED_ON_LEVEL);
    xTaskCreate(led_task, "status_led", 2048, NULL, 1, NULL);
#endif
}

void status_led_set(status_led_mode_t mode)
{
    s_mode = mode;
}
