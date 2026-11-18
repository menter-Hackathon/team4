// LiveKit のルーム plant-<id> に入り、切れたら入り直す（仕様 §9 の 1〜3）
#pragma once

#ifdef __cplusplus
extern "C" {
#endif

/// ルームへの接続を受け持つタスクを始める。Wi-Fi につながってから呼ぶ。
/// このタスクは、接続が失敗・切断されるたびに、間を空けて入り直す。
void room_start(void);

#ifdef __cplusplus
}
#endif
