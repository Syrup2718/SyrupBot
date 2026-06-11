"""核心流程:把一則進來的訊息,串成「上下文 → 決策 → 生成 → 後處理」。

這是主編排層。未來要加的功能(情緒、長期記憶、LLM 氣氛判斷)都是往這條
管線插一層,或替換某一層的實作,而不需要改動其他模組。
"""

from __future__ import annotations

import logging

from .context import ConversationStore
from .decision import DecisionEngine, IncomingMessage
from .llm import LLMClient
from .persona import Persona
from .postprocess import humanize, split_message

logger = logging.getLogger(__name__)


class ChatPipeline:
    """處理單則訊息,回傳要送回 Discord 的內容(可能多則,或不回)。"""

    def __init__(
        self,
        persona: Persona,
        context: ConversationStore,
        decision: DecisionEngine,
        llm: LLMClient,
    ) -> None:
        self._persona = persona
        self._context = context
        self._decision = decision
        self._llm = llm

    async def handle(self, msg: IncomingMessage) -> list[str]:
        """處理一則訊息;回傳要傳送的訊息段(空 list 代表不回)。"""
        # 先把非 bot 的訊息記進上下文(不論最後回不回,都要維持對話脈絡)。
        if not msg.author_is_bot:
            self._context.add(msg.channel_id, msg.author, msg.content, is_self=False)

        decision = await self._decision.decide(msg)
        logger.debug("頻道 %s 決策:%s", msg.channel_id, decision.reason)
        if not decision.should_reply:
            return []

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
            return []

        # 記下自己的發言,並更新冷卻。
        self._context.add(msg.channel_id, self._persona.name, reply, is_self=True)
        self._decision.record_reply(msg.channel_id)
        return split_message(reply)
