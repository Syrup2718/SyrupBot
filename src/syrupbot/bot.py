"""Discord 接線層:把 Discord 事件轉成框架無關的訊息,交給 pipeline。

這層刻意做薄——只負責「把 discord.Message 抽成 IncomingMessage、呼叫 pipeline、
把結果送回頻道」。所有判斷邏輯都在 decision / pipeline,不在這裡。
"""

from __future__ import annotations

import asyncio
import logging

import discord

from .decision import IncomingMessage
from .pipeline import ChatPipeline

logger = logging.getLogger(__name__)

# 模擬打字延遲:每個字約這麼多秒,並夾在一個合理區間,避免秒回或拖太久。
_TYPING_SECONDS_PER_CHAR = 0.05
_TYPING_MIN_SECONDS = 0.8
_TYPING_MAX_SECONDS = 4.0


class SyrupBot(discord.Client):
    """收 Discord 訊息事件,交給 pipeline 處理並回覆。"""

    def __init__(self, pipeline: ChatPipeline) -> None:
        intents = discord.Intents.default()
        # message_content 是特權 intent,需在 Developer Portal 開啟,
        # 否則讀不到一般訊息的文字內容。
        intents.message_content = True
        # 表情事件用的 intent(非特權,default 其實已含,這裡明示以免日後誤改)。
        intents.reactions = True
        super().__init__(intents=intents)
        self._pipeline = pipeline

    async def on_ready(self) -> None:
        logger.info("已登入:%s (id=%s)", self.user, self.user.id)

    async def on_message(self, message: discord.Message) -> None:
        # 永遠不處理自己的訊息(其他 bot 由 decision 層再過濾)。
        if message.author == self.user:
            return

        incoming = self._to_incoming(message)
        try:
            result = await self._pipeline.handle(incoming)
        except Exception:  # noqa: BLE001 — 單則訊息失敗不該拖垮整個 bot。
            logger.exception("處理訊息時發生錯誤")
            return

        # 先貼表情(若有),再(可能)回話。
        if result.reaction:
            await self._react(message, result.reaction)
        for chunk in result.replies:
            await self._send_like_human(message.channel, chunk)

    async def on_raw_reaction_add(
        self, payload: discord.RawReactionActionEvent
    ) -> None:
        """別人對某訊息按了表情 → 漿糖可能跟著按同一個。"""
        if self.user is not None and payload.user_id == self.user.id:
            return  # 自己按的,不要對自己的反應再反應(也避免無限迴圈)。
        if payload.member is not None and payload.member.bot:
            return  # 別的 bot 按的,略過。
        # 第一關(便宜):開關、頻道、機率。沒過就不抓訊息,省 API 呼叫。
        if not self._pipeline.should_follow_reaction(payload.channel_id):
            return

        channel = self.get_channel(payload.channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(payload.channel_id)
            except (discord.NotFound, discord.Forbidden):
                return
        if not isinstance(channel, discord.abc.Messageable):
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return

        # 第二關:找到對應的那個表情,確認自己沒按過、且已有足夠的人按。
        target = next(
            (r for r in message.reactions if str(r.emoji) == str(payload.emoji)),
            None,
        )
        if target is None or target.me:
            return
        if target.count < self._pipeline.follow_reaction_min_count:
            return
        await self._react(message, payload.emoji)

    async def _react(
        self, message: discord.Message, emoji: discord.PartialEmoji | str
    ) -> None:
        """貼表情,並吞掉常見錯誤(沒權限、外部自訂表情等),不要拖垮 bot。"""
        try:
            await message.add_reaction(emoji)
        except discord.HTTPException:
            logger.debug("貼表情失敗(可能沒權限或外部自訂表情)", exc_info=True)

    def _to_incoming(self, message: discord.Message) -> IncomingMessage:
        """把 discord.Message 抽成框架無關的 IncomingMessage。"""
        return IncomingMessage(
            channel_id=message.channel.id,
            author=message.author.display_name,
            content=self._clean_content(message),
            author_is_bot=message.author.bot,
            mentions_bot=self.user in message.mentions,
            is_reply_to_bot=self._is_reply_to_bot(message),
        )

    def _clean_content(self, message: discord.Message) -> str:
        """移除訊息中對自己的 @ 提及,取出實際文字。"""
        content = message.content
        for mention in (f"<@{self.user.id}>", f"<@!{self.user.id}>"):
            content = content.replace(mention, "")
        return content.strip()

    def _is_reply_to_bot(self, message: discord.Message) -> bool:
        """這則訊息是不是在回覆 bot 自己的訊息。"""
        ref = message.reference
        if ref is None:
            return False
        resolved = ref.resolved
        if isinstance(resolved, discord.Message):
            return resolved.author == self.user
        return False

    async def _send_like_human(
        self, channel: discord.abc.Messageable, text: str
    ) -> None:
        """加一點打字延遲再送出,讓回覆不要像機器人秒回。"""
        delay = min(
            max(len(text) * _TYPING_SECONDS_PER_CHAR, _TYPING_MIN_SECONDS),
            _TYPING_MAX_SECONDS,
        )
        async with channel.typing():
            await asyncio.sleep(delay)
        await channel.send(text)
