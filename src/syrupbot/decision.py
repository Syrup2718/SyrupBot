"""「是否要回覆」的決策引擎 —— SyrupBot 和傳統指令型 bot 的核心差異。

兩道關卡:
  第一關(免費的硬規則前置過濾):不是 bot、頻道允許、不是別人的指令、不在冷卻中。
  第二關(策略):
    - heuristic:用機率+加權規則判斷,不花 API token。
    - llm:讓模型讀上下文自己決定要不要插話(一次呼叫同時產生回覆)。
  捷徑:被 @ 提及、被 reply，或收到明確的連續追問時直接回。

策略由 config.decision_strategy 切換,介面不變,方便之後升級。
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from random import Random
from typing import Callable

from .channels import ChannelPolicy
from .config import Config
from .context import ConversationStore
from .llm import LLMClient
from .persona import Persona

logger = logging.getLogger(__name__)

# 視為「給別的 bot 的指令」而略過的訊息前綴。
_COMMAND_PREFIXES = ("/", "!", "?", ".", "$", "%", ">", "~")
_FOLLOW_UP_SECONDS = 60
_DIRECT_ADDRESS = re.compile(
    r"^(?:(?:那|所以|然後|不過|可是|對了|欸|請問|我想問(?:一下)?)[，,、\s]*)*"
    r"[你妳](?!們)"
)


@dataclass(frozen=True)
class IncomingMessage:
    """從 Discord 事件抽取出的、與框架無關的訊息資料。

    抽成純資料讓決策邏輯能脫離 discord.py 單獨測試。
    """

    channel_id: int
    author: str
    content: str
    author_is_bot: bool
    mentions_bot: bool
    is_reply_to_bot: bool
    author_id: int | None = None
    addresses_others: bool = False


@dataclass(frozen=True)
class ReplyDecision:
    """決策結果。``reply`` 在 llm 策略下會一併帶回生成的內容。"""

    should_reply: bool
    reason: str
    reply: str | None = None


class DecisionEngine:
    """決定每則訊息要不要回覆,並管理冷卻狀態。"""

    def __init__(
        self,
        config: Config,
        persona: Persona,
        llm: LLMClient,
        context: ConversationStore,
        *,
        policy: ChannelPolicy | None = None,
        rng: Random | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        self._persona = persona
        self._llm = llm
        self._context = context
        # 沒注入就依 config 自建一份(不持久化);production 會注入共用的實例。
        self._policy = policy or ChannelPolicy(initial=config.allowed_channel_ids)
        # rng 與 clock 可注入,讓決策在測試中可預測。
        self._rng = rng or Random()
        self._clock = clock or time.monotonic
        self._last_reply: dict[int, float] = {}
        self._last_decision: dict[int, float] = {}
        self._follow_up_targets: dict[int, tuple[int, float]] = {}

    async def decide(self, msg: IncomingMessage) -> ReplyDecision:
        """判斷這則訊息要不要回。"""
        if reason := self.skip_reason(msg):
            return ReplyDecision(False, reason)

        # ── 捷徑:直接被叫到就回(不受一般冷卻限制) ──
        if msg.mentions_bot or msg.is_reply_to_bot:
            self._follow_up_targets.pop(msg.channel_id, None)
            return ReplyDecision(True, "被直接 @ 或 reply")

        if self._take_follow_up(msg):
            return ReplyDecision(True, "同一位成員正在接續對話")

        # 一般訊息要受回覆冷卻限制,避免洗版。
        if self._in_cooldown(msg.channel_id):
            return ReplyDecision(False, "還在回覆冷卻時間內")

        # ── 第二關:依策略判斷 ──
        if self._config.decision_strategy == "llm":
            return await self._decide_llm(msg)
        return self._decide_heuristic(msg)

    def record_reply(self, channel_id: int) -> None:
        """回覆產生後更新一般插話的冷卻計時。"""
        self._last_reply[channel_id] = self._clock()

    def record_delivered_reply(self, msg: IncomingMessage) -> None:
        """Discord 送出回覆後，開始等待原發話者接話。"""
        if msg.author_id is not None:
            self._follow_up_targets[msg.channel_id] = (msg.author_id, self._clock())

    def clear_follow_up(self, channel_id: int) -> None:
        """結束某頻道的連續對話，保留防洗版的冷卻。"""
        self._follow_up_targets.pop(channel_id, None)

    def _take_follow_up(self, msg: IncomingMessage) -> bool:
        target = self._follow_up_targets.get(msg.channel_id)
        if target is None:
            return False
        author_id, sent_at = target
        if (
            msg.author_id != author_id
            or msg.addresses_others
            or self._clock() - sent_at >= _FOLLOW_UP_SECONDS
        ):
            self._follow_up_targets.pop(msg.channel_id, None)
            return False
        if not _DIRECT_ADDRESS.match(msg.content.lstrip()):
            return False
        # 先消耗資格，避免同一串快速連發觸發多個並行回覆。
        self._follow_up_targets.pop(msg.channel_id, None)
        return True

    # ── 內部:策略 ──

    def _decide_heuristic(self, msg: IncomingMessage) -> ReplyDecision:
        """機率 + 加權規則,不花 API token。"""
        score = self._config.reply_probability

        if msg.content.rstrip().endswith(("?", "？")):
            score += 0.25  # 像有人在拋問題
        if self._persona.mentioned_by_name(msg.content):
            score += 0.5  # 有人提到她的名字
        if self._persona.matches_interest(msg.content):
            score += 0.2  # 聊到她有興趣的話題
        if self._context.has_recent_self(msg.channel_id):
            score += 0.2  # 最近正在參與這串對話,延續話題
        if len(msg.content.strip()) <= 2:
            score -= 0.1  # 太短的訊息(「哈」「ok」)不太需要回

        score = max(0.0, min(score, 1.0))
        if self._rng.random() < score:
            return ReplyDecision(True, f"heuristic 命中(score={score:.2f})")
        return ReplyDecision(False, f"heuristic 未命中(score={score:.2f})")

    async def _decide_llm(self, msg: IncomingMessage) -> ReplyDecision:
        """讓 LLM 讀上下文自己決定;用判斷冷卻控制 API 成本。"""
        now = self._clock()
        last = self._last_decision.get(msg.channel_id, float("-inf"))
        if now - last < self._config.decision_cooldown_seconds:
            return ReplyDecision(False, "判斷冷卻中(控成本)")
        self._last_decision[msg.channel_id] = now

        transcript = self._context.transcript(msg.channel_id, self._persona.name)
        should_reply, reply = await self._llm.judge_and_reply(
            self._persona.system_prompt, transcript, channel_id=msg.channel_id,
            latest_message=(msg.author, msg.content),
        )
        if should_reply:
            return ReplyDecision(True, "LLM 判斷想插話", reply=reply)
        return ReplyDecision(False, "LLM 判斷不插話")

    # ── 內部:前置過濾輔助 ──

    def skip_reason(self, msg: IncomingMessage) -> str | None:
        """提供回合收集與決策共用的硬規則。"""
        if msg.author_is_bot:
            return "發話者是 bot"
        if not self._policy.is_allowed(msg.channel_id):
            return "頻道不在白名單"
        if self._looks_like_command(msg.content):
            return "看起來是給別的 bot 的指令"
        return None

    @staticmethod
    def _looks_like_command(content: str) -> bool:
        stripped = content.lstrip()
        return stripped.startswith(_COMMAND_PREFIXES)

    def _in_cooldown(self, channel_id: int) -> bool:
        last = self._last_reply.get(channel_id, float("-inf"))
        return (self._clock() - last) < self._config.reply_cooldown_seconds
