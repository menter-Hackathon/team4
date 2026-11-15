"""ふたまる 音声エージェントのエントリポイント。PlantConversation を LiveKit に配線する（仕様 §3, §5, §7）。

起動: uv run python -m agent.main console|dev|start
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterable

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    APIConnectOptions,
    ChatContext,
    ChatMessage,
    JobContext,
    JobProcess,
    ModelSettings,
    SessionConnectOptions,
    StopResponse,
    TurnHandlingOptions,
    cli,
    llm,
    tts,
)
from livekit.plugins import silero

from agent.config import Config, load_config
from agent.plant.conversation import PlantConversation
from agent.plant.expression import ExpressionParser
from agent.plant.prompt import FALLBACK_REPLY, NUDGE_INSTRUCTION, WAKE_GREETING_INSTRUCTION
from agent.plant.store import PlantStore
from agent.plant.timeutil import jst_now
from agent.plugins.llm import create_json_completer, create_llm
from agent.plugins.openai_tts import create_tts
from agent.plugins.whisper_stt import create_stt

load_dotenv()
logger = logging.getLogger("futamaru")

ROOM_PREFIX = "plant-"
TICK_SECONDS = 1.0


class FutamaruAgent(Agent):
    def __init__(self, conv: PlantConversation) -> None:
        super().__init__(instructions="")
        self._conv = conv

    async def on_user_turn_completed(self, turn_ctx: ChatContext, new_message: ChatMessage) -> None:
        original = new_message.text_content or ""
        decision = await self._conv.on_user_turn(original)
        if decision.instructions is not None:
            await self.update_instructions(decision.instructions)
            # 今回の返答も新しいプロンプトで作るため、一時コンテキストも差し替える
            turn_ctx.items[:] = self.chat_ctx.copy().items
        if decision.kind == "ignore":
            raise StopResponse()  # ユーザー発話は履歴にも残らない
        if decision.kind == "greet":
            self.session.generate_reply(instructions=WAKE_GREETING_INSTRUCTION)
            raise StopResponse()
        if decision.text != original:
            new_message.content = [decision.text]

    async def llm_node(
        self, chat_ctx: ChatContext, tools: list[llm.FunctionTool], model_settings: ModelSettings
    ) -> AsyncIterable[llm.ChatChunk | str]:
        parser = ExpressionParser()
        emitted = False

        def handle(text: str) -> str:
            nonlocal emitted
            parsed = parser.feed(text)
            if parsed.expression is not None:
                self._conv.express(parsed.expression)
            emitted = emitted or bool(parsed.text)
            return parsed.text

        try:
            async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
                if isinstance(chunk, str):
                    cleaned = handle(chunk)
                    if cleaned:
                        yield cleaned
                elif isinstance(chunk, llm.ChatChunk) and chunk.delta and chunk.delta.content:
                    chunk.delta.content = handle(chunk.delta.content)
                    yield chunk
                else:
                    yield chunk
        except Exception:
            logger.exception("LLM error")
            if not emitted:
                self._conv.express("neutral")
                yield FALLBACK_REPLY
                return
        tail = parser.flush()
        if tail.expression is not None:
            self._conv.express(tail.expression)
        if tail.text:
            yield tail.text


def prewarm(proc: JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load()


server = AgentServer(setup_fnc=prewarm)


def resolve_plant_id(room_name: str, cfg: Config) -> str | None:
    if room_name.startswith(ROOM_PREFIX) and len(room_name) > len(ROOM_PREFIX):
        return room_name[len(ROOM_PREFIX) :]
    return cfg.dev_plant_id  # console モードなど（開発用）。無ければ退出


@server.rtc_session()
async def entrypoint(ctx: JobContext) -> None:
    cfg = load_config()
    plant_id = resolve_plant_id(ctx.job.room.name, cfg)
    if plant_id is None:
        logger.info("room %s is not a plant room; leaving", ctx.job.room.name)
        return

    device_identity = f"device-{plant_id}"
    store = PlantStore(cfg.db_path)

    async def send_expression(name: str) -> None:
        await ctx.room.local_participant.perform_rpc(
            destination_identity=device_identity,
            method="set_expression",
            payload=json.dumps({"expression": name}),
        )

    conv = PlantConversation(
        store=store,
        plant_id=plant_id,
        complete_json=create_json_completer(cfg),
        send_expression=send_expression,
        clock=jst_now,
        wake_words=cfg.wake_words,
        sleep_after=cfg.sleep_after,
    )

    session = AgentSession(
        stt=create_stt(cfg),
        llm=create_llm(cfg),
        tts=create_tts(cfg),
        vad=ctx.proc.userdata["vad"],
        turn_handling=TurnHandlingOptions(turn_detection="vad", preemptive_generation={"enabled": False}),
        user_away_timeout=None,
        conn_options=SessionConnectOptions(
            stt_conn_options=APIConnectOptions(timeout=cfg.stt_timeout),
            llm_conn_options=APIConnectOptions(max_retry=0, timeout=cfg.llm_timeout),
            tts_conn_options=APIConnectOptions(timeout=cfg.tts_timeout),
            # GPU サーバーが一時的に落ちても植物を終了させない
            max_unrecoverable_errors=1_000_000,
        ),
    )
    agent = FutamaruAgent(conv)

    @session.on("user_state_changed")
    def _on_user_state(ev) -> None:
        conv.on_user_speaking(ev.new_state == "speaking")

    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:
        conv.on_agent_state(ev.new_state)

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        item = ev.item
        if isinstance(item, ChatMessage) and item.role == "assistant":
            conv.on_plant_utterance(item.text_content or "")

    @session.on("error")
    def _on_error(ev) -> None:
        if isinstance(ev.source, tts.TTS):
            conv.express("sleepy")

    @ctx.room.on("participant_connected")
    def _on_join(p: rtc.RemoteParticipant) -> None:
        if p.identity == device_identity:
            conv.express("sleepy")

    @ctx.room.on("participant_disconnected")
    def _on_leave(p: rtc.RemoteParticipant) -> None:
        if p.identity == device_identity:
            asyncio.create_task(conv.close())

    async def shutdown() -> None:
        await conv.close()
        store.close()

    ctx.add_shutdown_callback(shutdown)

    await ctx.connect()
    await conv.start()
    await session.start(agent=agent, room=ctx.room)

    async def ticker() -> None:
        while True:
            await asyncio.sleep(TICK_SECONDS)
            try:
                result = await conv.tick()
                if result == "nudge":
                    session.generate_reply(instructions=NUDGE_INSTRUCTION)
                elif result == "slept":
                    await agent.update_chat_ctx(ChatContext.empty())  # 眠ったら会話履歴を捨てる
            except Exception:
                logger.exception("tick failed")

    ticker_task = asyncio.create_task(ticker())
    ctx.add_shutdown_callback(lambda: _cancel(ticker_task))


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cli.run_app(server)
