"""核心流程:把一則進來的訊息,串成「上下文 → 決策 → 生成 → 後處理」。

這是主編排層。未來要加的功能(情緒、長期記憶、LLM 氣氛判斷)都是往這條
管線插一層,或替換某一層的實作,而不需要改動其他模組。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .context import ConversationStore
from .decision import DecisionEngine, IncomingMessage
from .llm import LLMClient
from .persona import Persona
from .postprocess import humanize, split_message
from .reaction import ReactionEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PipelineResult:
    """一則訊息處理後的結果:要送的訊息段,以及(可選的)要貼在原訊息上的表情。"""

    replies: list[str] = field(default_factory=list)
    reaction: str | None = None


class ChatPipeline:
    """處理單則訊息,回傳要送回 Discord 的內容(可能多則、或不回、或只貼表情)。"""

    def __init__(
        self,
        persona: Persona,
        context: ConversationStore,
        decision: DecisionEngine,
        llm: LLMClient,
        reaction: ReactionEngine,
    ) -> None:
        self._persona = persona
        self._context = context
        self._decision = decision
        self._llm = llm
        self._reaction = reaction

    async def handle(self, msg: IncomingMessage) -> PipelineResult:
        """處理一則訊息;回傳要傳送的訊息段與要貼的表情。"""
        # 先把非 bot 的訊息記進上下文(不論最後回不回,都要維持對話脈絡)。
        if not msg.author_is_bot:
            self._context.add(msg.channel_id, msg.author, msg.content, is_self=False)

        # 「要不要貼表情」與「要不要回話」彼此獨立:可能只貼表情不回話。
        reaction = self._reaction.decide_self_reaction(msg)

        decision = await self._decision.decide(msg)
        logger.debug("頻道 %s 決策:%s", msg.channel_id, decision.reason)
        if not decision.should_reply:
            return PipelineResult(reaction=reaction)

        # llm 策略可能已經把回覆生出來了;heuristic 策略則在這裡才生成。
        if decision.reply is not None:
            raw_reply = decision.reply
        else:
            transcript = self._context.transcript(msg.channel_id, self._persona.name)
            raw_reply = await self._llm.generate_reply(
                self._persona.system_prompt, transcript
            )

        reply = humanize(raw_reply)
        if not reply:
            return PipelineResult(reaction=reaction)

        # 記下自己的發言,並更新冷卻。
        self._context.add(msg.channel_id, self._persona.name, reply, is_self=True)
        self._decision.record_reply(msg.channel_id)
        return PipelineResult(replies=split_message(reply), reaction=reaction)

    def should_follow_reaction(self, channel_id: int) -> bool:
        """別人按了表情時,是否(機率上)要跟著按。"""
        return self._reaction.should_follow_reaction(channel_id)

    @property
    def follow_reaction_min_count(self) -> int:
        """至少要有幾個人按過,漿糖才跟著按。"""
        return self._reaction.follow_min_count
