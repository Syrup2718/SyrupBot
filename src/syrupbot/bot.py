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
            replies = await self._pipeline.handle(incoming)
        except Exception:  # noqa: BLE001 — 單則訊息失敗不該拖垮整個 bot。
            logger.exception("處理訊息時發生錯誤")
            return

        for chunk in replies:
            await self._send_like_human(message.channel, chunk)

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
