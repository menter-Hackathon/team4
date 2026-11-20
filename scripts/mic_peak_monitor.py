"""鉢（ESP32）の USB のログから、マイクの大きさと大事な状態だけを表示し続ける（配線の確認用）。

鉢のファームウェア（firmware/main/relay.c）は、10 秒ごとに次の行をログに出す。

    I (14338) relay: Mic frames sent: 500 (muted 0), mic peak: 148, voice received: 0 bytes

このスクリプトは、その行を「mic peak:    148  muted: 0」のように短くして表示する。
mic peak の読み方（firmware/README.md の「ログ」）:
    - 静かな部屋で 50〜300 くらい: 正常
    - 0 が続く: マイクの音の線（G7 → D3）がつながっていない
    - 32767 近くが続く: どこかの線の接触が悪く、雑音が入っている（2026-11-20 は G7 → D3 だった）

使い方（XIAO を USB でつないで。idf.py monitor など、同じ USB を開いているものは先に閉じる）:

    uv run --with pyserial python -m scripts.mic_peak_monitor
    uv run --with pyserial python -m scripts.mic_peak_monitor --port /dev/cu.usbmodem1101

pyserial はこのスクリプトでしか使わないので、プロジェクトの依存には足さず、--with でその場で使う。
USB が一瞬切れても（線を差し直したときなど）止まらず、つながり直すのを待って読み続ける。Ctrl+C で終わる。
"""

from __future__ import annotations

import argparse
import glob
import re
import time

# マイクの大きさ以外に表示する行（止まった・つながった、などの状態）。ファームウェアのログの言葉に合わせる
STATUS_WORDS = (
    "Stopped",  # 配線の間違いなどで止まった（main.c）
    "I2C lines",  # 起動時の I2C の線の検査（board.c）
    "stuck",
    "No pull-up",
    "Audio ready",
    "Relay is ready",  # 中継につながった（relay.c）
    "Disconnected",
    "Reconnecting",
    "Connecting to Wi-Fi",
)
# これより大きい mic peak が 10 秒ごとに続くなら、声ではなく雑音とみなす。
# 2026-11-20 の実機では、接触が悪いと 32767 近くに張り付き、正常なら静かなとき 100 前後だった
NOISE_PEAK = 20000
# XIAO の USB の名前（Mac と Linux）。Windows では --port で COM3 などを渡す
PORT_PATTERNS = ("/dev/cu.usbmodem*", "/dev/ttyACM*")

_PEAK = re.compile(r"mic peak: (\d+)")
_MUTED = re.compile(r"\(muted (\d+)\)")


def describe(line: str) -> str | None:
    """ログの 1 行を、表示する短い文にする。表示しない行なら None を返す。

    例:
        "... Mic frames sent: 500 (muted 0), mic peak: 148, ..." → "mic peak:    148  muted: 0"
        "... mic peak: 0, ..."                                    → 「無音」の目印つき
        "... futamaru: Stopped: audio board not ready ..."        → その行をそのまま（字下げして）
        "I (752) wifi: ..."                                       → None（表示しない）
    """
    # 1. マイクの大きさの行。目印（無音・雑音）を付ける
    peak_match = _PEAK.search(line)
    if peak_match:
        peak = int(peak_match.group(1))
        muted_match = _MUTED.search(line)
        # muted は 2026-11-20 より前のファームウェアには無い
        muted = muted_match.group(1) if muted_match else "-"
        mark = ""
        if peak == 0:
            mark = "  ← 無音（マイクの線がつながっていない）"
        elif peak > NOISE_PEAK:
            mark = "  ← 雑音（どこかの線の接触が悪い）"
        return f"mic peak: {peak:>6}  muted: {muted}{mark}"
    # 2. 大事な状態の行は、そのまま見せる
    if any(word in line for word in STATUS_WORDS):
        return "  " + line.strip()
    # 3. それ以外は表示しない
    return None


def find_port() -> str | None:
    """USB につながった XIAO の名前を探す。見つからなければ None。"""
    for pattern in PORT_PATTERNS:
        ports = sorted(glob.glob(pattern))
        if ports:
            return ports[0]
    return None


def main(argv: list[str] | None = None) -> None:
    """USB のログを読み続け、describe() が返した文を表示する。"""
    # pyserial はここで読み込む（テストで pyserial が無くても describe() を試せるように）
    import serial

    parser = argparse.ArgumentParser(description="鉢のマイクの大きさを表示し続ける")
    parser.add_argument("--port", help="XIAO の USB の名前（省略すると自動で探す）")
    args = parser.parse_args(argv)

    while True:
        # 1. XIAO の USB を探す。見つかるまで 1 秒ごとに探し直す
        port_name = args.port or find_port()
        if port_name is None:
            print("鉢（XIAO）が USB で見つかりません。つながるのを待っています…", flush=True)
            while port_name is None:
                time.sleep(1)
                port_name = find_port()
        # 2. 開いて 1 行ずつ読む。USB が切れたら（例外が出たら）1 に戻る
        try:
            with serial.Serial(port_name, 115200, timeout=1) as port:
                print(f"{port_name} を見ています。10 秒ごとに mic peak が出ます", flush=True)
                while True:
                    text = describe(port.readline().decode("utf-8", "replace"))
                    if text is not None:
                        print(text, flush=True)
        except serial.SerialException:
            print("USB が切れました。つながるのを待っています…", flush=True)
            time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
