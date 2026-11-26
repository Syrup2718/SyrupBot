"""核心流程:把一則進來的訊息,串成「上下文 → 決策 → 生成 → 後處理」。

這是主編排層。未來要加的功能(情緒、長期記憶、LLM 氣氛判斷)都是往這條
管線插一層,或替換某一層的實作,而不需要改動其他模組。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from random import Random

from .context import ConversationStore
from .decision import DecisionEngine, IncomingMessage
from .gifs import GifPicker, GifSelection
from .llm import CompletionStats, LLMClient
from . import monitor as events
from .lull import LullWatcher
from .monitor import Monitor
from .persona import Persona
from .postprocess import (
    humanize,
    is_chat_replay,
    link_mentions,
    split_chat_reply,
    unlink_mentions,
)
from .reaction import ReactionEngine
from .turns import combine_turn

logger = logging.getLogger(__name__)

# 冷場時附加在 prompt 後面的提醒,讓模型自己決定有沒有值得說的話。
_LULL_NOTE = (
    "\n\n群組已經安靜了一陣子，最後一則訊息不是你說的。"
    "只有在你想到和剛才話題自然相關、或真的有趣的事時才開口，一句就好。"
    "不要硬找話題，也不要問大家怎麼不講話。沒有想說的就選擇不說。"
)


@dataclass(frozen=True)
class PipelineResult:
    """一則訊息處理後的結果:要送的訊息段,以及(可選的)要貼在原訊息上的表情。"""

    replies: list[str] = field(default_factory=list)
    reaction: str | None = None
    context_revision: int = 0
    gif: GifSelection | None = None


@dataclass(frozen=True)
class LLMStatus:
    """提供狀態畫面所需的模型與上下文摘要。"""

    model: str
    decision_model: str
    context_count: int
    context_limit: int
    in_flight: int
    last_completion: CompletionStats | None


class ChatPipeline:
    """接收回合、規劃回覆，以及記錄實際送出的文字。"""

    def __init__(
        self,
        persona: Persona,
        context: ConversationStore,
        decision: DecisionEngine,
        llm: LLMClient,
        reaction: ReactionEngine,
        gifs: GifPicker | None = None,
        rng: Random | None = None,
        lull: LullWatcher | None = None,
        monitor: Monitor | None = None,
        tag_members: bool = True,
    ) -> None:
        self._persona = persona
        self._context = context
        self._decision = decision
        self._llm = llm
        self._reaction = reaction
        self._gifs = gifs
        self._rng = rng or Random()
        self._lull = lull
        self.monitor = monitor or Monitor()
        # 允許角色用「@名字」tag 最近講過話的人。
        self._tag_members = tag_members
        # 看過的 bot 帳號,後台用來標示哪些發言者是 bot。
        self._bot_ids: set[int] = set()

    async def handle(self, msg: IncomingMessage) -> PipelineResult:
        """在沒有回合收集器時，規劃單則訊息的表情與回覆。"""
        reaction = self.reaction_for(msg)
        result = await self.handle_turn((msg,), self.context_revision(msg.channel_id))
        return replace(result, reaction=reaction)

    def context_revision(self, channel_id: int) -> int:
        return self._context.revision(channel_id)

    def can_merge(self, msg: IncomingMessage) -> bool:
        return not msg.addresses_others and self._decision.skip_reason(msg) is None

    def should_quote(self, msg: IncomingMessage, *, interrupted: bool) -> bool:
        """送出時要不要引用回覆 msg(interrupted:之後有別人插話)。"""
        return self._decision.should_quote(msg, interrupted=interrupted)

    def lull_target(self, channel_id: int) -> IncomingMessage:
        """冷場開口時她接的是最後一則訊息;引用回覆時用它當目標。"""
        history = self._context.messages(channel_id)
        last_id = history[-1].message_id if history and not history[-1].is_self else None
        return lull_message(channel_id, last_id)

    def bot_block_reason(self, msg: IncomingMessage) -> str | None:
        """別的 bot 的這則訊息不能回的原因;可以走一般判斷時回傳 None。"""
        return self._decision.bot_block_reason(msg)

    def observe(
        self, msg: IncomingMessage, *, edited: bool = False, note: str = ""
    ) -> str | None:
        """只把訊息記進上下文、不做任何回覆判斷(用於其他 bot 的發言)。

        回傳沒記下的原因;有記下時回傳 None。編輯過的訊息會依 ID 更新原本那筆。
        """
        reason = None
        if not msg.content.strip():
            reason = "沒有文字內容(可能只有圖片,或是還沒編輯完成的延遲回應)"
        elif not self._decision.channel_allowed(msg.channel_id):
            reason = "頻道不在白名單"
        if reason is not None:
            self.monitor.record(
                events.BOT_IGNORED, msg.channel_id, author=msg.author,
                text=msg.content, detail=reason,
            )
            return reason
        if msg.author_is_bot and msg.author_id is not None:
            self._bot_ids.add(msg.author_id)
        if msg.message_id is not None:
            updated = self._context.upsert(
                msg.channel_id, msg.message_id, msg.author, msg.content, msg.author_id
            )
        else:
            updated = False
            self._context.add(
                msg.channel_id, msg.author, msg.content, is_self=False,
                author_id=msg.author_id,
            )
        detail = "編輯後更新上下文" if updated else (
            "編輯後補記進上下文" if edited else "已記進上下文"
        )
        if note:
            detail = f"{detail}(不回:{note})"
        self.monitor.record(
            events.BOT_SEEN, msg.channel_id, author=msg.author, text=msg.content,
            detail=detail,
        )
        return None

    def note_activity(self, msg: IncomingMessage) -> float | None:
        """成員講話時更新冷場計時,回傳安靜多久算冷場(不需要偵測時回 None)。"""
        if self._lull is None or self._decision.skip_reason(msg) is not None:
            return None
        self._lull.note_message(msg.channel_id)
        return self._lull.delay_for(msg.channel_id)

    async def handle_lull(self, channel_id: int, revision: int) -> PipelineResult:
        """冷場時給角色一次開口的機會;由模型決定有沒有值得說的話。"""
        history = self._context.messages(channel_id)
        if not history or history[-1].is_self or self.context_revision(channel_id) != revision:
            return PipelineResult()
        if not self._decision.should_break_lull(channel_id):
            return PipelineResult()
        transcript = self._context.transcript(channel_id, self._persona.name)
        should_reply, raw_reply = await self._llm.judge_and_reply(
            self._persona.judge_prompt_for_turn(self._rng.random) + _LULL_NOTE,
            transcript, channel_id=channel_id,
        )
        if not should_reply or self.context_revision(channel_id) != revision:
            self.monitor.record(events.LULL, channel_id, detail="冷場了,但模型選擇不說")
            return PipelineResult()
        reply = humanize(raw_reply)
        if not reply or self._is_replay(lull_message(channel_id), reply):
            return PipelineResult()
        self.monitor.record(events.LULL, channel_id, text=reply, detail="冷場主動開口")
        logger.debug("頻道 %s 冷場,角色主動開口", channel_id)
        return PipelineResult(
            replies=split_chat_reply(self._link(channel_id, reply, None)),
            context_revision=revision,
        )

    def reaction_for(self, msg: IncomingMessage) -> str | None:
        return self._reaction.decide_self_reaction(msg)

    async def handle_turn(
        self, messages: tuple[IncomingMessage, ...], revision: int
    ) -> PipelineResult:
        """依順序記錄入站訊息，再對整個回合判斷一次。"""
        msg = combine_turn(messages)
        channel_id = msg.channel_id
        if self.context_revision(channel_id) != revision:
            return PipelineResult()
        for item in messages:
            if item.author_is_bot and item.author_id is not None:
                self._bot_ids.add(item.author_id)
            self._context.add(
                channel_id, item.author, item.content, is_self=False,
                message_id=item.message_id, author_id=item.author_id,
            )
            self.monitor.record(
                events.BOT_SEEN if item.author_is_bot else events.HUMAN,
                channel_id, author=item.author, text=item.content,
                detail="已記進上下文,會判斷要不要回" if item.author_is_bot else "",
            )

        auto_gif = None
        if self._gifs is not None and self._decision.skip_reason(msg) is None:
            gif = self._gifs.for_request(msg)
            if gif is not None:
                return PipelineResult(gif=gif, context_revision=revision)
            auto_gif = self._gifs.for_reply(msg)
        gif_only = PipelineResult(gif=auto_gif, context_revision=revision)

        decision = await self._decision.decide(msg)
        logger.debug("頻道 %s 決策:%s", msg.channel_id, decision.reason)
        self.monitor.record(
            events.DECISION, channel_id, author=msg.author,
            detail=("要回:" if decision.should_reply else "不回:") + decision.reason,
            replied=decision.should_reply,
        )
        if self._context.revision(msg.channel_id) != revision:
            return PipelineResult()
        if not decision.should_reply:
            return gif_only

        # llm 策略可能已經把回覆生出來了;heuristic 策略則在這裡才生成。
        if decision.reply is not None:
            raw_reply = decision.reply
        else:
            transcript = self._context.transcript(msg.channel_id, self._persona.name)
            raw_reply = await self._llm.generate_reply(
                self._persona.prompt_for_turn(self._rng.random), transcript,
                channel_id=msg.channel_id,
                latest_message=(msg.author, msg.content),
            )

        if self._context.revision(msg.channel_id) != revision:
            return PipelineResult()

        reply = humanize(raw_reply)
        if self._is_replay(msg, reply):
            logger.debug("頻道 %s 回覆重貼對話，改用當前訊息重試", msg.channel_id)
            raw_reply = await self._llm.generate_reply(
                self._persona.system_prompt,
                "",
                channel_id=msg.channel_id,
                latest_message=(msg.author, msg.content),
            )
            reply = humanize(raw_reply)
            if self._context.revision(msg.channel_id) != revision:
                return PipelineResult()
            if self._is_replay(msg, reply):
                return gif_only
        if not reply:
            return gif_only

        return replace(
            gif_only, replies=split_chat_reply(self._link(channel_id, reply, msg.author_id))
        )

    def _link(self, channel_id: int, reply: str, skip_id: int | None) -> str:
        """把回覆裡的「@名字」換成真的 tag(只限最近講過話的人)。"""
        if not self._tag_members:
            return reply
        return link_mentions(reply, self._context.speakers(channel_id), skip_id=skip_id)

    def clear_context(self, channel_id: int) -> int:
        """清空本頻道的短期記憶與連續對話。"""
        self._decision.clear_follow_up(channel_id)
        return self._context.clear(channel_id)

    def is_current_reply(self, channel_id: int, result: PipelineResult) -> bool:
        return self._context.revision(channel_id) == result.context_revision

    def _is_replay(self, msg: IncomingMessage, reply: str) -> bool:
        history = self._context.messages(msg.channel_id)
        return is_chat_replay(
            reply,
            speakers={self._persona.name, msg.author, *(item.author for item in history)},
            previous_replies=(item.content for item in history if item.is_self),
        )

    def record_delivered_reply(
        self, msg: IncomingMessage, result: PipelineResult, sent: list[str],
        *, gif_sent: bool = False,
    ) -> None:
        """送出成功才記錄文字，網路失敗時只保存已確認送出的段落。"""
        if not self.is_current_reply(msg.channel_id, result):
            return
        parts = sent
        if gif_sent and result.gif is not None:
            if self._gifs is not None:
                self._gifs.record_sent(msg.channel_id)
            parts = [*sent, f"[傳了{result.gif.label} GIF]"]
        if not parts:
            return
        # 存進上下文時把 <@ID> 換回「@名字」,模型才看得懂自己 tag 了誰。
        text = unlink_mentions("\n".join(parts), self._context.speakers(msg.channel_id))
        self._context.add(msg.channel_id, self._persona.name, text, is_self=True)
        self.monitor.record(events.REPLY, msg.channel_id, author=self._persona.name, text=text)
        if sent:
            self._decision.record_reply(msg.channel_id)
            if msg.author_is_bot:
                self._decision.record_bot_reply(msg.channel_id)
        self._decision.record_delivered_reply(msg)

    def snapshot(self) -> dict:
        """後台用的目前狀態:角色、策略、各頻道上下文與節奏,以及最近事件。"""
        channels = []
        for channel_id in self._context.channel_ids():
            count, limit = self._context.usage(channel_id)
            channels.append({
                "id": str(channel_id),  # 超過 JavaScript 整數精度,用字串傳
                "allowed": self._decision.channel_allowed(channel_id),
                "fatigue": round(self._decision.fatigue(channel_id), 2),
                "cooldown_left": round(self._decision.cooldown_left(channel_id), 1),
                "context_count": count,
                "context_limit": limit,
                "messages": [
                    {
                        "author": m.author,
                        "content": m.content,
                        "is_self": m.is_self,
                        "is_bot": m.author_id in self._bot_ids,
                    }
                    for m in self._context.messages(channel_id)
                ],
            })
        return {
            "persona": self._persona.name,
            "strategy": self._decision.strategy,
            "model": self._llm.model,
            "decision_model": self._llm.decision_model,
            "in_flight": self._llm.in_flight,
            "started_at": self.monitor.started_at,
            "counts": dict(self.monitor.counts),
            "decisions": dict(self.monitor.decisions),
            "settings": self._decision.settings(),
            "tag_members": self._tag_members,
            "channels": channels,
            "events": self.monitor.events(),
        }

    def llm_status(self, channel_id: int) -> LLMStatus:
        """取得目前的頻道上下文和最近一次模型請求。"""
        count, limit = self._context.usage(channel_id)
        return LLMStatus(
            model=self._llm.model,
            decision_model=self._llm.decision_model,
            context_count=count,
            context_limit=limit,
            in_flight=self._llm.in_flight,
            last_completion=self._llm.last_stats(channel_id),
        )

    def should_follow_reaction(self, channel_id: int) -> bool:
        """別人按了表情時,是否(機率上)要跟著按。"""
        return self._reaction.should_follow_reaction(channel_id)

    @property
    def follow_reaction_min_count(self) -> int:
        """至少要有幾個人按過,漿糖才跟著按。"""
        return self._reaction.follow_min_count


def lull_message(channel_id: int, message_id: int | None = None) -> IncomingMessage:
    """冷場開口沒有觸發訊息,用一則空白訊息代表,送出後不會進入接話模式。

    message_id 是她接話的那則訊息,送出時可能被引用回覆。
    """
    return IncomingMessage(channel_id, "", "", False, False, False, message_id=message_id)
