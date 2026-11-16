# ふたまるのエージェント（脳のうち、会話を進める部分）のイメージ。
# LLM（vLLM）と STT（Speaches）は別のコンテナで動かし、compose.yaml でまとめて起動する。
# 手順書: docs/deploy.md

FROM python:3.12-slim

# uv（Python のパッケージ管理ツール）を、公式イメージからバージョンを固定してコピーする
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv

WORKDIR /app

# uv がパッケージを入れる仮想環境の場所。PATH に足して、python コマンドでそのまま使えるようにする
ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    FUTAMARU_DB_PATH=/app/data/futamaru.db

# 1. 依存だけ先に入れる。pyproject.toml と uv.lock が変わらない限り、この層はキャッシュが使われ、
#    コードを直したときの再ビルドが速くなる
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# 2. エージェントのコードを入れる（.env やテストは .dockerignore で除いている）
COPY agent ./agent
COPY scripts ./scripts
RUN uv sync --locked --no-dev

# 3. VAD（声が出ているかの判定）などのモデルを、ビルドの時点でダウンロードしておく。
#    起動のたびにダウンロードしなくて済み、起動が速くなる
RUN python -m agent.main download-files

# 会話ログ・記憶の DB を置く場所。compose.yaml でホストの ./data をここにつなぐ
VOLUME ["/app/data"]

# start: LiveKit Cloud につなぎ、ESP32 がルームに入ってくるのを待つ（本番用のモード）
CMD ["python", "-m", "agent.main", "start"]
