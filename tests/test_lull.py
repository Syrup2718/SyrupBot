"""冷場偵測與冷場開口的測試。"""

from __future__ import annotations

from dataclasses import replace
from random import Random
from unittest.mock import AsyncMock, Mock

import pytest

from syrupbot.context import ConversationStore
from syrupbot.decision import DecisionEngine
from syrupbot.lull import LullWatcher
from syrupbot.persona import Persona
from syrupbot.pipeline import ChatPipeline

from test_decision import Clock, FakeLLM, _base_config, _FixedRandom, _msg


def _watcher(clock, **overrides):
    config = _base_config(lull_min_seconds=60.0, lull_max_seconds=600.0, **overrides)
    return LullWatcher(config, clock=clock)


def test_needs_enough_recent_messages():
    clock = Clock()
    watcher = _watcher(clock)
    watcher.note_message(1)
    clock.now += 10
    watcher.note_message(1)
    assert watcher.delay_for(1) is None
    clock.now += 10
    watcher.note_message(1)
    assert watcher.delay_for(1) == 60.0  # 平均 10 秒 × 4 = 40,低於下限


def test_delay_follows_conversation_pace_within_bounds():
    clock = Clock()
    watcher = _watcher(clock)
    for _ in range(3):
        watcher.note_message(1)
        clock.now += 50
    assert watcher.delay_for(1) == 200.0  # 平均 50 秒 × 4

    slow = _watcher(clock)
    for _ in range(3):
        slow.note_message(1)
        clock.now += 200
    assert slow.delay_for(1) == 600.0  # 上限


def test_stale_messages_and_disabled_feature():
    clock = Clock()
    watcher = _watcher(clock)
    for _ in range(3):
        watcher.note_message(1)
    clock.now += 700
    assert watcher.delay_for(1) is None

    off = _watcher(clock, lull_probability=0.0)
    for _ in range(3):
        off.note_message(1)
    assert off.delay_for(1) is None


def _pipeline(llm, rng_value=0.0, **config):
    persona = Persona.load("syrup")
    cfg = _base_config(reply_cooldown_seconds=0.0, **config)
    context = ConversationStore(20)
    decision = DecisionEngine(
        cfg, persona, llm, context, rng=_FixedRandom(rng_value), clock=Clock()
    )
    pipeline = ChatPipeline(
        persona, context, decision, llm, Mock(), rng=Random(0),
        lull=LullWatcher(cfg, clock=Clock()),
    )
    return pipeline, context, decision


@pytest.mark.asyncio
async def test_lull_lets_model_speak_once_when_dice_hit():
    llm = FakeLLM(should_reply=True, reply="欸那個新歌你們聽了嗎")
    pipeline, context, _ = _pipeline(llm, rng_value=0.0, lull_probability=0.5)
    context.add(1, "小明", "今天好累", is_self=False)
    result = await pipeline.handle_lull(1, 0)
    assert result.replies == ["欸那個新歌你們聽了嗎"]
    assert llm.judge_calls == 1


@pytest.mark.asyncio
async def test_lull_skips_when_self_spoke_last_or_dice_miss():
    llm = FakeLLM(should_reply=True)
    pipeline, context, _ = _pipeline(llm, rng_value=0.9, lull_probability=0.5)
    context.add(1, "小明", "今天好累", is_self=False)
    assert (await pipeline.handle_lull(1, 0)).replies == []

    context.add(1, "漿糖", "辛苦了", is_self=True)
    hit, ctx, _ = _pipeline(llm, rng_value=0.0, lull_probability=1.0)
    ctx.add(1, "漿糖", "辛苦了", is_self=True)
    assert (await hit.handle_lull(1, 0)).replies == []
    assert llm.judge_calls == 0


@pytest.mark.asyncio
async def test_lull_respects_model_refusal_and_cleared_context():
    llm = FakeLLM(should_reply=False)
    pipeline, context, _ = _pipeline(llm, lull_probability=1.0)
    context.add(1, "小明", "今天好累", is_self=False)
    assert (await pipeline.handle_lull(1, 0)).replies == []

    context.clear(1)
    context.add(1, "小明", "新話題", is_self=False)
    assert (await pipeline.handle_lull(1, 0)).replies == []  # 版本已變


def test_note_activity_ignores_skipped_messages():
    llm = FakeLLM()
    pipeline, _, _ = _pipeline(llm, lull_probability=1.0)
    assert pipeline.note_activity(_msg(content="!play 晴天")) is None
    assert pipeline.note_activity(replace(_msg(), author_is_bot=True)) is None


def test_note_activity_without_watcher_is_noop():
    persona = Persona.load("syrup")
    pipeline = ChatPipeline(
        persona, ConversationStore(5), Mock(), Mock(generate_reply=AsyncMock()), Mock()
    )
    assert pipeline.note_activity(_msg()) is None
