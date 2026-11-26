"""後台、事件紀錄,以及「看得到別的 bot」相關的測試。"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from aiohttp.test_utils import TestClient, TestServer
from discord.components import Container, TextDisplay

from syrupbot.bot import SyrupBot, _component_texts, _embed_texts
from syrupbot.channels import ChannelPolicy
from syrupbot.context import ConversationStore
from syrupbot.dashboard import Dashboard
from syrupbot.decision import IncomingMessage
from syrupbot.monitor import Monitor
from syrupbot.persona import Persona
from syrupbot.pipeline import ChatPipeline

_BOT_MSG = IncomingMessage(
    1234567890123456789, "音樂bot", "正在播放:晴天", True, False, False,
    author_id=99, message_id=555,
)


def _pipeline(allowed: bool = True) -> tuple[ChatPipeline, ConversationStore]:
    context = ConversationStore(20)
    decision = Mock(
        channel_allowed=Mock(return_value=allowed),
        fatigue=Mock(return_value=0.5),
        cooldown_left=Mock(return_value=3.0),
        strategy="heuristic",
    )
    llm = Mock(model="deepseek-chat", in_flight=0)
    pipeline = ChatPipeline(Persona.load("syrup"), context, decision, llm, Mock())
    return pipeline, context


def test_embed_fields_and_footer_become_text():
    embed = discord.Embed(title="現在播放", description="晴天 - 周杰倫")
    embed.set_author(name="音樂bot")
    embed.add_field(name="時長", value="4:29")
    embed.set_footer(text="由小明點播")
    assert _embed_texts([embed]) == ["音樂bot", "現在播放", "晴天 - 周杰倫", "時長:4:29", "由小明點播"]


def test_components_v2_text_is_collected_recursively():
    container = Container(
        {"type": 17, "id": 1, "components": [{"type": 10, "content": "內層文字", "id": 2}]},
        None,
    )
    top = TextDisplay({"type": 10, "content": "外層文字", "id": 3})
    assert _component_texts([top, container]) == ["外層文字", "內層文字"]


def test_observe_records_reason_for_ignored_bot_messages():
    pipeline, context = _pipeline()
    assert pipeline.observe(replace(_BOT_MSG, content="  ")) is not None
    event = pipeline.monitor.events()[0]
    assert event["kind"] == "bot_ignored"
    assert "沒有文字內容" in event["detail"]

    blocked, _ = _pipeline(allowed=False)
    assert blocked.observe(_BOT_MSG) == "頻道不在白名單"


def test_edited_bot_message_updates_the_same_context_entry():
    pipeline, context = _pipeline()
    assert pipeline.observe(replace(_BOT_MSG, content="思考中…")) is None
    pipeline.observe(_BOT_MSG, edited=True)
    messages = context.messages(_BOT_MSG.channel_id)
    assert [m.content for m in messages] == ["正在播放:晴天"]
    assert pipeline.monitor.events()[0]["detail"] == "編輯後更新上下文"


async def test_on_message_edit_reads_deferred_bot_reply():
    pipeline, context = _pipeline()
    bot = SyrupBot(pipeline, ChannelPolicy())
    bot._connection.user = SimpleNamespace(id=42)
    bot._to_incoming = lambda m: m.incoming
    bot._clean_content = lambda m: m.incoming.content
    author = SimpleNamespace(bot=True, id=99)
    before = SimpleNamespace(author=author, incoming=replace(_BOT_MSG, content=""))
    after = SimpleNamespace(author=author, incoming=_BOT_MSG)

    await bot.on_message_edit(before, after)
    assert context.messages(_BOT_MSG.channel_id)[-1].content == "正在播放:晴天"

    own = SimpleNamespace(author=SimpleNamespace(bot=True, id=42), incoming=_BOT_MSG)
    await bot.on_message_edit(before, own)  # 自己的編輯不處理
    assert len(context.messages(_BOT_MSG.channel_id)) == 1


def test_snapshot_uses_string_ids_and_includes_events():
    pipeline, _ = _pipeline()
    pipeline.observe(_BOT_MSG)
    state = pipeline.snapshot()
    assert state["persona"] == "漿糖"
    channel = state["channels"][0]
    assert channel["id"] == "1234567890123456789"
    assert channel["messages"][0]["content"] == "正在播放:晴天"
    assert state["events"][0]["channel_id"] == "1234567890123456789"


def test_monitor_keeps_latest_events_first_and_bounded():
    monitor = Monitor(max_events=2, clock=iter([0.0, 1.0, 2.0, 3.0]).__next__)
    for text in ("一", "二", "三"):
        monitor.record("human", 1, text=text)
    assert [e["text"] for e in monitor.events()] == ["三", "二"]


async def test_dashboard_serves_page_and_state_with_token():
    dashboard = Dashboard("127.0.0.1", 0, token="secret")
    app = dashboard.build_app(lambda: {"persona": "漿糖"})
    async with TestClient(TestServer(app)) as client:
        assert (await client.get("/api/state")).status == 401
        res = await client.get("/api/state?token=secret")
        assert res.status == 200
        assert (await res.json()) == {"persona": "漿糖"}
        page = await client.get("/", headers={"X-Token": "secret"})
        assert "SyrupBot 後台" in await page.text()


async def test_dashboard_without_token_is_open():
    app = Dashboard("127.0.0.1", 0).build_app(lambda: {"ok": True})
    async with TestClient(TestServer(app)) as client:
        assert (await (await client.get("/api/state")).json()) == {"ok": True}


async def test_bot_dashboard_state_adds_channel_names():
    pipeline, _ = _pipeline()
    pipeline.observe(_BOT_MSG)
    bot = SyrupBot(pipeline, ChannelPolicy(), dashboard=Mock(start=AsyncMock()))
    bot.get_channel = lambda cid: SimpleNamespace(name="閒聊", guild=SimpleNamespace(name="糖群"))
    state = bot.dashboard_state()
    assert state["channel_names"] == {"1234567890123456789": "糖群/閒聊"}


def test_monitor_counts_survive_the_event_limit():
    monitor = Monitor(max_events=1)
    monitor.record("human", 1)
    monitor.record("decision", 1, replied=True)
    monitor.record("decision", 1, replied=False)
    assert monitor.counts == {"human": 1, "decision": 2}
    assert monitor.decisions == {"reply": 1, "skip": 1}
    assert len(monitor.events()) == 1


def test_snapshot_marks_bot_speakers():
    pipeline, _ = _pipeline()
    pipeline.observe(_BOT_MSG)
    message = pipeline.snapshot()["channels"][0]["messages"][0]
    assert message["is_bot"] is True
