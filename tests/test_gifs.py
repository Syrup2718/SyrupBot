"""驗證 GIF 的挑選、頻道限制與送達記錄。"""

from dataclasses import replace
from random import Random
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from syrupbot.bot import SyrupBot
from syrupbot.channels import ChannelPolicy
from syrupbot.config import Config
from syrupbot.context import ConversationStore
from syrupbot.decision import DecisionEngine, IncomingMessage
from syrupbot.gifs import GifPicker
from syrupbot.persona import Persona
from syrupbot.pipeline import ChatPipeline
from syrupbot.reaction import ReactionEngine


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("GIFS_ENABLED", "true")
    monkeypatch.setenv("GIF_REPLY_PROBABILITY", "1")
    monkeypatch.setenv("GIF_COOLDOWN_SECONDS", "90")
    monkeypatch.setenv("ALLOWED_CHANNEL_IDS", "")
    monkeypatch.setenv("DECISION_STRATEGY", "heuristic")
    monkeypatch.setenv("REPLY_PROBABILITY", "0")
    return Config.from_env()


def test_gif_picker_gates_automatic_replies_and_honors_requests(config):
    persona = Persona.load("syrup")
    clock = SimpleNamespace(now=10.0)
    picker = GifPicker(config, persona, clock=lambda: clock.now, rng=Random(0))
    greeting = IncomingMessage(1, "甲", "早安", False, True, False)
    assert picker.for_reply(greeting).label == "月薪貓・站起來"

    picker.record_sent(1)
    assert picker.for_reply(greeting) is None
    assert picker.for_reply(replace(greeting, channel_id=2)).label == "月薪貓・站起來"
    clock.now += 90
    assert picker.for_reply(greeting).label == "月薪貓・站起來"
    assert picker.for_reply(replace(greeting, content="早安 但我心情不好")) is None

    request = replace(greeting, content="發個月薪貓跳舞的貼圖")
    assert picker.for_request(request).label == "月薪貓・捂嘴跳舞"
    assert picker.for_request(replace(request, mentions_bot=False)) is None
    assert picker.for_request(replace(request, content="不要發 GIF")) is None
    assert picker.for_request(replace(request, content="不要發月薪貓")) is None
    disabled = GifPicker(replace(config, gifs_enabled=False), persona)
    assert disabled.for_request(request) is None
    assert disabled.for_reply(greeting) is None


async def test_gif_request_respects_policy_and_sends_without_model(config):
    persona = Persona.load("syrup")
    context = ConversationStore(20)
    policy = ChannelPolicy(initial=frozenset({1}))
    llm = SimpleNamespace(generate_reply=AsyncMock())
    decision = DecisionEngine(config, persona, llm, context, policy=policy)
    reactions = ReactionEngine(config, persona, policy=policy)
    picker = GifPicker(config, persona, rng=Random(0))
    pipeline = ChatPipeline(persona, context, decision, llm, reactions, picker)
    msg = IncomingMessage(1, "甲", "發個月薪貓滑手機貼圖", False, True, False, author_id=7)

    blocked = await pipeline.handle_turn((replace(msg, channel_id=2),), 0)
    assert not blocked.replies and blocked.gif is None

    result = await pipeline.handle_turn((msg,), 0)
    assert not result.replies and result.gif.label == "月薪貓・滑手機"
    assert len(context.messages(1)) == 1
    llm.generate_reply.assert_not_awaited()

    bot = SyrupBot(pipeline, policy)
    channel = SimpleNamespace(send=AsyncMock())
    await bot._send_replies(channel, msg, result)
    channel.send.assert_awaited_once_with(result.gif.url)
    assert context.messages(1)[-1].content == "[傳了月薪貓・滑手機 GIF]"
    assert picker.for_reply(replace(msg, content="跳舞")) is None

    policy.allow(3)
    chat = replace(msg, channel_id=3, content="我真的特別愛你", mentions_bot=False)
    automatic = await pipeline.handle_turn((chat,), 0)
    assert not automatic.replies
    assert automatic.gif.label == "月薪貓・捂嘴跳舞"
    llm.generate_reply.assert_not_awaited()


@pytest.mark.parametrize("series", ["月薪貓", "月薪猫", "月薪喵"])
def test_salary_cat_series_can_be_requested_without_gif_word(config, series):
    persona = Persona.load("syrup")
    picker = GifPicker(config, persona, rng=Random(0))
    request = IncomingMessage(1, "甲", f"發{series}", False, True, False)
    gif = picker.for_request(request)
    assert gif is not None
    assert gif.label.startswith("月薪貓・")
    assert gif.url.startswith("https://media.tenor.com/")


def test_more_specific_sticker_keyword_wins(config):
    picker = GifPicker(config, Persona.load("syrup"), rng=Random(0))
    request = IncomingMessage(1, "甲", "想看廁所滑手機的貼圖", False, True, False)
    gif = picker.for_request(request)
    assert gif is not None and gif.label == "月薪貓・蹲馬桶"
