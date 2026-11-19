// XIAO の基板上の LED（GPIO21）で、今どこまでつながっているかを見せる
#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/// LED の光り方。上から順に、つながる手順が進んだことを表す。
typedef enum {
    STATUS_LED_OFF,         ///< 消灯
    STATUS_LED_ERROR,       ///< 2 回ずつ点滅: 設定の不足・配線の間違いなどで止まっている
    STATUS_LED_NETWORK,     ///< 速い点滅: Wi-Fi につないでいる
    STATUS_LED_RELAY,       ///< ゆっくり点滅: サーバーの中継（relay）につないでいる・ready を待っている
    STATUS_LED_READY,       ///< 点灯: 中継から ready が来た。話しかけられる
} status_led_mode_t;

/// LED を使えるようにする（Kconfig で LED を切っていれば何もしない）
void status_led_init(void);

/// LED の光り方を変える。どのタスクから呼んでもよい。
void status_led_set(status_led_mode_t mode);

#ifdef __cplusplus
}
#endif
