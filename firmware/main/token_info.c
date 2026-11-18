// トークンに書かれたルーム名を読み出す（どの株として入るかをログに出すため）
//
// 2 台の鉢に別々のトークンを書き込むので、取り違えるとどちらの株か分からなくなる。
// 起動時に「plant-a1 に入る」とログに出して、書き込み間違いに気づけるようにする。
//
// JWT は「ヘッダ.中身.署名」を「.」でつないだ文字列で、中身は base64url で書かれた JSON。
// LiveKit のトークンでは、中身の {"video": {"room": "plant-a1", ...}} にルーム名がある
// （scripts/issue_token.py の VideoGrants(room=...)）。

#include "token_info.h"

#include <stdlib.h>
#include <string.h>

#include "cJSON.h"
#include "mbedtls/base64.h"

bool token_get_room(const char *token, char *out, size_t out_size)
{
    if (token == NULL || out == NULL || out_size == 0) {
        return false;
    }

    // 1. 「.」で区切られた 2 つ目（中身）の場所を探す
    const char *start = strchr(token, '.');
    if (start == NULL) {
        return false;
    }
    start++;
    const char *end = strchr(start, '.');
    if (end == NULL) {
        return false;
    }
    size_t len = (size_t)(end - start);

    // 2. base64url を普通の base64 に直す（'-' → '+'、'_' → '/'、長さが 4 の倍数になるまで '=' を足す）
    size_t padded = len + (4 - len % 4) % 4;
    char *b64 = malloc(padded + 1);
    if (b64 == NULL) {
        return false;
    }
    for (size_t i = 0; i < len; i++) {
        char c = start[i];
        b64[i] = (c == '-') ? '+' : (c == '_') ? '/' : c;
    }
    for (size_t i = len; i < padded; i++) {
        b64[i] = '=';
    }
    b64[padded] = '\0';

    // 3. base64 を JSON の文字列に戻す
    size_t json_cap = padded / 4 * 3 + 1;
    unsigned char *json = malloc(json_cap);
    size_t json_len = 0;
    bool ok = false;
    if (json != NULL &&
        mbedtls_base64_decode(json, json_cap - 1, &json_len, (const unsigned char *)b64, padded) == 0) {
        json[json_len] = '\0';

        // 4. JSON の video.room を読む
        cJSON *root = cJSON_Parse((const char *)json);
        const cJSON *video = cJSON_GetObjectItemCaseSensitive(root, "video");
        const cJSON *room = cJSON_GetObjectItemCaseSensitive(video, "room");
        if (cJSON_IsString(room) && room->valuestring != NULL) {
            strlcpy(out, room->valuestring, out_size);
            ok = true;
        }
        cJSON_Delete(root);
    }

    free(json);
    free(b64);
    return ok;
}
