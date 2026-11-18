// トークンに書かれたルーム名を読み出す（どの株として入るかをログに出すため）
#pragma once

#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/// LiveKit のトークン（JWT）から、入れるルーム名（例: "plant-a1"）を取り出す。
///
/// トークンの署名は確かめない（確かめるのは LiveKit のサーバー）。ログに出すためだけに使う。
///
/// 引数:
///   token: トークンの文字列
///   out / out_size: ルーム名を書き込む場所とその大きさ
/// 戻り値: 取り出せたら true。トークンの形がおかしければ false。
bool token_get_room(const char *token, char *out, size_t out_size);

#ifdef __cplusplus
}
#endif
