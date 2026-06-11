"""表情回應決策測試。

用可注入的假亂數讓「要不要按、按哪個」完全可預測,不碰 Discord。
"""

from __future__ import annotations

from random import Random

from syrupbot.config import Config
from syrupbot.decision import IncomingMessage
from syrupbot.persona import Persona
from syrupbot.reaction import ReactionEngine


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


class _FixedRandom(Random):
    """random() 永遠回固定值的假亂數來源。"""

    def __init__(self, value: float) -> None:
        super().__init__()
        self._value = value

    def random(self) -> float:  # type: ignore[override]
        return self._value


def _engine(config: Config, rng=None) -> ReactionEngine:
    persona = Persona.load("syrup")
    return ReactionEngine(config, persona, rng=rng)


# ── 自己決定要不要按 ──


def test_keyword_picks_matching_emoji() -> None:
    # 機率擲骰回 0.0 必過關;訊息含「蛋糕」→ 用對應表情。
    engine = _engine(
        _base_config(self_reaction_probability=1.0), rng=_FixedRandom(0.0)
    )
    assert engine.decide_self_reaction(_msg(content="想吃蛋糕")) == "🍰"


def test_no_keyword_falls_back_to_pool() -> None:
    # 對不到關鍵字時,從通用表情池挑一個(必為池內成員)。
    engine = _engine(
        _base_config(self_reaction_probability=1.0), rng=_FixedRandom(0.0)
    )
    persona = Persona.load("syrup")
    emoji = engine.decide_self_reaction(_msg(content="嗯嗯好喔"))
    assert emoji in persona.reaction_emojis


def test_probability_gate_blocks_reaction() -> None:
    # 擲骰回 0.99 > 機率 0.1 → 不按。
    engine = _engine(
        _base_config(self_reaction_probability=0.1), rng=_FixedRandom(0.99)
    )
    assert engine.decide_self_reaction(_msg(content="想吃蛋糕")) is None


def test_disabled_never_reacts() -> None:
    engine = _engine(
        _base_config(reactions_enabled=False, self_reaction_probability=1.0),
        rng=_FixedRandom(0.0),
    )
    assert engine.decide_self_reaction(_msg(content="想吃蛋糕")) is None


def test_skips_other_bots_and_empty() -> None:
    engine = _engine(
        _base_config(self_reaction_probability=1.0), rng=_FixedRandom(0.0)
    )
    assert engine.decide_self_reaction(_msg(content="蛋糕", author_is_bot=True)) is None
    assert engine.decide_self_reaction(_msg(content="   ")) is None


def test_disallowed_channel_no_reaction() -> None:
    engine = _engine(
        _base_config(
            self_reaction_probability=1.0, allowed_channel_ids=frozenset({999})
        ),
        rng=_FixedRandom(0.0),
    )
    assert engine.decide_self_reaction(_msg(channel_id=1, content="蛋糕")) is None


# ── 跟著別人一起按 ──


def test_follow_passes_probability() -> None:
    engine = _engine(
        _base_config(follow_reaction_probability=0.5), rng=_FixedRandom(0.1)
    )
    assert engine.should_follow_reaction(1) is True


def test_follow_blocked_by_probability() -> None:
    engine = _engine(
        _base_config(follow_reaction_probability=0.3), rng=_FixedRandom(0.99)
    )
    assert engine.should_follow_reaction(1) is False


def test_follow_respects_disabled_and_channel() -> None:
    off = _engine(
        _base_config(reactions_enabled=False, follow_reaction_probability=1.0),
        rng=_FixedRandom(0.0),
    )
    assert off.should_follow_reaction(1) is False

    scoped = _engine(
        _base_config(
            follow_reaction_probability=1.0, allowed_channel_ids=frozenset({999})
        ),
        rng=_FixedRandom(0.0),
    )
    assert scoped.should_follow_reaction(1) is False
