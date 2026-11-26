"""決策引擎測試。

用假的 LLM、可注入的亂數與時鐘,讓「該不該回」的判斷完全可預測,
不需要連 Discord 或真的呼叫 API。
"""

from __future__ import annotations

from dataclasses import replace
from random import Random

import pytest

from syrupbot.config import Config
from syrupbot.context import ConversationStore
from syrupbot.decision import DecisionEngine, IncomingMessage
from syrupbot.persona import Persona


class FakeLLM:
    """記錄是否被呼叫,並回傳預設的判斷結果。"""

    def __init__(self, should_reply: bool = True, reply: str = "嗨窩") -> None:
        self._should_reply = should_reply
        self._reply = reply
        self.judge_calls = 0

    async def judge_and_reply(
        self, system_prompt: str, transcript: str, *, channel_id: int | None = None,
        latest_message: tuple[str, str] | None = None,
    ):
        self.judge_calls += 1
        return self._should_reply, self._reply


class Clock:
    """可手動推進的假時鐘。"""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _base_config(**overrides) -> Config:
    defaults = dict(
        discord_token="x",
        deepseek_api_key="x",
        deepseek_base_url="https://api.deepseek.com",
        model="deepseek-chat",
        decision_model="deepseek-chat",
        temperature=1.1,
        persona="syrup",
        decision_strategy="heuristic",
        reply_probability=0.08,
        reply_cooldown_seconds=20.0,
        decision_cooldown_seconds=10.0,
        max_context_messages=20,
        allowed_channel_ids=frozenset(),
        log_level="INFO",
    )
    defaults.update(overrides)
    return Config(**defaults)


def _msg(**overrides) -> IncomingMessage:
    defaults = dict(
        channel_id=1,
        author="小明",
        content="今天天氣真好",
        author_is_bot=False,
        mentions_bot=False,
        is_reply_to_bot=False,
    )
    defaults.update(overrides)
    return IncomingMessage(**defaults)


def _engine(config: Config, llm=None, rng=None, clock=None):
    persona = Persona.load("syrup")
    context = ConversationStore(config.max_context_messages)
    return (
        DecisionEngine(
            config,
            persona,
            llm or FakeLLM(),
            context,
            rng=rng,
            clock=clock,
        ),
        context,
    )


# ── 第一關:硬規則前置過濾 ──


@pytest.mark.asyncio
async def test_ignores_other_bots() -> None:
    engine, _ = _engine(_base_config())
    decision = await engine.decide(_msg(author_is_bot=True))
    assert decision.should_reply is False


@pytest.mark.asyncio
async def test_ignores_disallowed_channel() -> None:
    config = _base_config(allowed_channel_ids=frozenset({999}))
    engine, _ = _engine(config)
    decision = await engine.decide(_msg(channel_id=1))
    assert decision.should_reply is False


@pytest.mark.asyncio
async def test_ignores_command_for_other_bot() -> None:
    engine, _ = _engine(_base_config())
    decision = await engine.decide(_msg(content="!play 音樂"))
    assert decision.should_reply is False


# ── 捷徑:被直接叫到 ──


@pytest.mark.asyncio
async def test_always_replies_when_mentioned() -> None:
    # 即使機率為 0,被 @ 也要回。
    engine, _ = _engine(_base_config(reply_probability=0.0))
    decision = await engine.decide(_msg(mentions_bot=True))
    assert decision.should_reply is True


@pytest.mark.asyncio
async def test_always_replies_when_replied_to() -> None:
    engine, _ = _engine(_base_config(reply_probability=0.0))
    decision = await engine.decide(_msg(is_reply_to_bot=True))
    assert decision.should_reply is True


# ── 第二關:heuristic 策略 ──


@pytest.mark.asyncio
async def test_heuristic_replies_when_score_beats_roll() -> None:
    # rng 固定回 0.0,只要分數 > 0 就會回。
    engine, _ = _engine(_base_config(reply_probability=0.5), rng=_FixedRandom(0.0))
    decision = await engine.decide(_msg())
    assert decision.should_reply is True


@pytest.mark.asyncio
async def test_heuristic_skips_when_roll_beats_score() -> None:
    # rng 固定回 0.99,基礎機率 0.08 不可能命中。
    engine, _ = _engine(_base_config(reply_probability=0.08), rng=_FixedRandom(0.99))
    decision = await engine.decide(_msg())
    assert decision.should_reply is False


@pytest.mark.asyncio
async def test_name_mention_boosts_score() -> None:
    # 基礎機率 0,但提到名字 +0.5;擲骰 0.3 應命中。
    engine, _ = _engine(_base_config(reply_probability=0.0), rng=_FixedRandom(0.3))
    decision = await engine.decide(_msg(content="syrup 你看這個"))
    assert decision.should_reply is True


# ── 冷卻 ──


@pytest.mark.asyncio
async def test_reply_cooldown_blocks_followups() -> None:
    clock = Clock()
    engine, _ = _engine(
        _base_config(reply_probability=1.0), rng=_FixedRandom(0.0), clock=clock
    )
    first = await engine.decide(_msg())
    assert first.should_reply is True

    engine.record_reply(1)
    clock.now += 5  # 還在 20 秒冷卻內
    second = await engine.decide(_msg())
    assert second.should_reply is False

    clock.now += 20  # 冷卻過了
    third = await engine.decide(_msg())
    assert third.should_reply is True


@pytest.mark.asyncio
async def test_mention_bypasses_cooldown() -> None:
    clock = Clock()
    engine, _ = _engine(_base_config(), clock=clock)
    engine.record_reply(1)
    clock.now += 1  # 還在冷卻內
    decision = await engine.decide(_msg(mentions_bot=True))
    assert decision.should_reply is True


# ── 連續對話 ──


@pytest.mark.parametrize("strategy", ["heuristic", "llm"])
@pytest.mark.parametrize("content", ["你呢？", "欸，那你覺得勒", "我想問妳剛剛說的"])
async def test_follow_up_bypasses_decision_and_cooldown(strategy, content):
    llm = FakeLLM(should_reply=False)
    engine, _ = _engine(_base_config(decision_strategy=strategy), llm=llm, clock=Clock())
    engine.record_reply(1)
    turn = _msg(author_id=10, content=content)
    assert not (await engine.decide(turn)).should_reply

    engine.record_delivered_reply(_msg(author_id=10))
    decision = await engine.decide(turn)
    assert decision.should_reply
    assert decision.reply is None
    assert llm.judge_calls == 0
    assert not (await engine.decide(turn)).should_reply

    engine.record_delivered_reply(turn)
    assert (await engine.decide(turn)).should_reply


@pytest.mark.parametrize("interruption", [
    {"author_id": 99},
    {"author_id": 10, "addresses_others": True},
])
async def test_follow_up_ignores_other_recipients_and_interruptions(interruption):
    engine, _ = _engine(_base_config(), clock=Clock(), rng=_FixedRandom(0.99))
    engine.record_reply(1)
    engine.record_delivered_reply(_msg(author_id=10))

    assert not (await engine.decide(_msg(content="你呢？", **interruption))).should_reply
    assert not (await engine.decide(_msg(author_id=10, content="你覺得呢？"))).should_reply


async def test_follow_up_requires_fresh_and_direct_address():
    clock = Clock()
    engine, _ = _engine(_base_config(), clock=clock, rng=_FixedRandom(0.99))
    engine.record_delivered_reply(_msg(author_id=10))
    for content in ("你們覺得呢？", "我在找你的作業", "他問『你會嗎』"):
        assert not (await engine.decide(_msg(author_id=10, content=content))).should_reply

    clock.now += 60
    assert not (await engine.decide(_msg(author_id=10, content="你會嗎？"))).should_reply


async def test_follow_up_is_channel_local():
    engine, _ = _engine(_base_config(), clock=Clock(), rng=_FixedRandom(0.99))
    engine.record_delivered_reply(_msg(author_id=10))
    follow_up = _msg(author_id=10, content="你呢？")
    assert not (await engine.decide(replace(follow_up, channel_id=2))).should_reply
    assert (await engine.decide(follow_up)).should_reply


# ── 第二關:llm 策略 ──


@pytest.mark.asyncio
async def test_llm_strategy_uses_model_decision() -> None:
    config = _base_config(decision_strategy="llm")
    llm = FakeLLM(should_reply=True, reply="欸我也覺得")
    engine, _ = _engine(config, llm=llm)
    decision = await engine.decide(_msg())
    assert decision.should_reply is True
    assert decision.reply == "欸我也覺得"
    assert llm.judge_calls == 1


@pytest.mark.asyncio
async def test_llm_decision_cooldown_skips_api_call() -> None:
    config = _base_config(decision_strategy="llm")
    clock = Clock()
    llm = FakeLLM()
    engine, _ = _engine(config, llm=llm, clock=clock)

    await engine.decide(_msg())
    assert llm.judge_calls == 1

    clock.now += 2  # 還在 10 秒判斷冷卻內 → 不應再呼叫 API
    decision = await engine.decide(_msg())
    assert decision.should_reply is False
    assert llm.judge_calls == 1


class _FixedRandom(Random):
    """random() 永遠回固定值的假亂數來源。"""

    def __init__(self, value: float) -> None:
        super().__init__()
        self._value = value

    def random(self) -> float:  # type: ignore[override]
        return self._value


# ── hybrid 策略與話量疲勞 ──


@pytest.mark.asyncio
async def test_hybrid_asks_llm_only_when_look_roll_hits() -> None:
    llm = FakeLLM(should_reply=True, reply="真的假的")
    config = _base_config(decision_strategy="hybrid", look_probability=0.3)

    miss, _ = _engine(config, llm=llm, rng=_FixedRandom(0.5))
    assert (await miss.decide(_msg())).should_reply is False
    assert llm.judge_calls == 0

    hit, _ = _engine(config, llm=llm, rng=_FixedRandom(0.1))
    decision = await hit.decide(_msg())
    assert decision.should_reply is True
    assert decision.reply == "真的假的"
    assert llm.judge_calls == 1


@pytest.mark.asyncio
async def test_hybrid_respects_llm_refusal() -> None:
    llm = FakeLLM(should_reply=False)
    config = _base_config(decision_strategy="hybrid", look_probability=1.0)
    engine, _ = _engine(config, llm=llm, rng=_FixedRandom(0.0))
    assert (await engine.decide(_msg())).should_reply is False
    assert llm.judge_calls == 1


@pytest.mark.asyncio
async def test_fatigue_lowers_score_and_recovers_over_time() -> None:
    clock = Clock()
    config = _base_config(
        reply_probability=0.6, reply_cooldown_seconds=0.0,
        talk_fatigue_half_life_seconds=100.0,
    )
    engine, _ = _engine(config, rng=_FixedRandom(0.4), clock=clock)
    assert (await engine.decide(_msg())).should_reply is True

    engine.record_reply(1)
    engine.record_reply(1)
    assert engine.fatigue(1) == pytest.approx(2.0)
    # 0.6 / (1 + 2) = 0.2 < 0.4 → 講太多,不插話。
    assert (await engine.decide(_msg())).should_reply is False

    clock.now += 300  # 三個半衰期後疲勞剩 0.25
    assert engine.fatigue(1) == pytest.approx(0.25)
    assert (await engine.decide(_msg())).should_reply is True


@pytest.mark.asyncio
async def test_fatigue_is_channel_local_and_can_be_disabled() -> None:
    clock = Clock()
    engine, _ = _engine(_base_config(), clock=clock)
    engine.record_reply(1)
    assert engine.fatigue(1) > 0
    assert engine.fatigue(2) == 0

    off, _ = _engine(_base_config(talk_fatigue_half_life_seconds=0.0), clock=clock)
    off.record_reply(1)
    assert off.fatigue(1) == 0


@pytest.mark.asyncio
async def test_fatigue_does_not_block_mentions() -> None:
    engine, _ = _engine(_base_config(reply_cooldown_seconds=0.0), clock=Clock())
    for _ in range(10):
        engine.record_reply(1)
    assert (await engine.decide(_msg(mentions_bot=True))).should_reply is True


@pytest.mark.asyncio
async def test_llm_strategy_skips_judge_when_fatigued() -> None:
    llm = FakeLLM()
    config = _base_config(decision_strategy="llm", reply_cooldown_seconds=0.0)
    engine, _ = _engine(config, llm=llm, rng=_FixedRandom(0.6), clock=Clock())
    engine.record_reply(1)  # 倍率 0.5 < 擲骰 0.6
    assert (await engine.decide(_msg())).should_reply is False
    assert llm.judge_calls == 0
