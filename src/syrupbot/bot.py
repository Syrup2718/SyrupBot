"""Discord 接線層:把 Discord 事件轉成框架無關的訊息,交給 pipeline。

這層刻意做薄——只負責「把 discord.Message 抽成 IncomingMessage、呼叫 pipeline、
把結果送回頻道」。所有判斷邏輯都在 decision / pipeline,不在這裡。
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands

from .channels import ChannelPolicy
from .config import DEFAULT_TURN_WINDOW_SECONDS
from .decision import IncomingMessage
from .pipeline import ChatPipeline, LLMStatus, PipelineResult, lull_message
from .turns import PendingTurn, TurnScheduler

logger = logging.getLogger(__name__)

# 模擬打字延遲:每個字約這麼多秒,並夾在一個合理區間,避免秒回或拖太久。
_TYPING_SECONDS_PER_CHAR = 0.05
_TYPING_MIN_SECONDS = 0.8
_TYPING_MAX_SECONDS = 4.0
_NEXT_CHUNK_DELAY = 0.6


def _count(value: int | None) -> str:
    return f"{value:,}" if value is not None else "未提供"


def _llm_status_embed(status: LLMStatus) -> discord.Embed:
    embed = discord.Embed(title="漿糖的模型狀態", colour=0xE68BA7)
    models = f"生成 `{status.model[:250]}`"
    if status.decision_model != status.model:
        models += f"\n決策 `{status.decision_model[:250]}`"
    embed.add_field(name="模型", value=models, inline=False)
    embed.add_field(
        name="本頻道上下文",
        value=f"**{status.context_count:,} / {status.context_limit:,}** 則訊息",
    )
    embed.add_field(name="進行中的請求", value=f"**{status.in_flight:,}**（全 bot）")

    stats = status.last_completion
    if stats is None:
        embed.add_field(
            name="最近一次請求",
            value="本頻道自啟動以來還沒有完成的請求。",
            inline=False,
        )
        return embed

    kind = "判斷是否插話" if stats.judge else "生成回覆"
    embed.add_field(
        name="最近一次請求",
        value=f"{kind} · `{stats.model[:250]}` · **{stats.seconds:.2f} 秒**",
        inline=False,
    )
    embed.add_field(
        name="Token 用量",
        value=(
            f"輸入 **{_count(stats.prompt_tokens)}**"
            f" · 輸出 **{_count(stats.completion_tokens)}**"
            f" · 總計 **{_count(stats.total_tokens)}**"
        ),
        inline=False,
    )
    if stats.prompt_tps is not None:
        embed.add_field(name="輸入處理速度", value=f"**{stats.prompt_tps:.1f}** tok/s")
    if stats.generation_tps is not None:
        embed.add_field(name="生成速度", value=f"**{stats.generation_tps:.1f}** tok/s")
    elif stats.completion_tokens is not None and stats.seconds > 0:
        average = stats.completion_tokens / stats.seconds
        embed.add_field(name="平均輸出速度", value=f"**{average:.1f}** tok/s（含等候）")
    else:
        embed.add_field(name="生成速度", value="未提供")
    return embed


def nickname_change(
    current_nick: str | None, account_name: str, wanted: str | None
) -> tuple[bool, str | None]:
    """回傳 ``(要不要改, 新暱稱)``。

    想要的名字剛好等於帳號名稱時清掉暱稱(新暱稱為 None),顯示結果相同但比較乾淨。
    Discord 暱稱上限 32 字,超過就截斷。
    """
    if wanted is None:
        return False, None
    target = None if wanted == account_name else wanted[:32]
    return current_nick != target, target


class SyrupBot(discord.Client):
    """收 Discord 訊息事件,交給 pipeline 處理並回覆。"""

    def __init__(
        self, pipeline: ChatPipeline, channel_policy: ChannelPolicy,
        *, turn_window_seconds: float = DEFAULT_TURN_WINDOW_SECONDS,
        nickname: str | None = None,
    ) -> None:
        intents = discord.Intents.default()
        # message_content 是特權 intent,需在 Developer Portal 開啟,
        # 否則讀不到一般訊息的文字內容。
        intents.message_content = True
        # 表情事件用的 intent(非特權,default 其實已含,這裡明示以免日後誤改)。
        intents.reactions = True
        super().__init__(intents=intents)
        self._pipeline = pipeline
        self._policy = channel_policy
        self._turns = TurnScheduler(turn_window_seconds)
        # 每個伺服器要顯示的暱稱(通常是角色名);None 代表不動暱稱。
        self._nickname = nickname
        self._turn_locks: dict[int, asyncio.Lock] = {}
        self._lull_waits: dict[int, asyncio.Task[None]] = {}
        self.tree = app_commands.CommandTree(self)
        self._synced = False
        self._register_commands()

    async def on_ready(self) -> None:
        logger.info("已登入:%s (id=%s)", self.user, self.user.id)
        # 把斜線指令同步到所在的每個伺服器(per-guild 同步是即時的)。
        # 需要邀請時帶 applications.commands scope,否則這裡會失敗。
        if not self._synced:
            try:
                for guild in self.guilds:
                    self.tree.copy_global_to(guild=guild)
                    await self.tree.sync(guild=guild)
                self._synced = True
                logger.info("已同步斜線指令到 %d 個伺服器", len(self.guilds))
            except discord.HTTPException:
                logger.exception(
                    "同步斜線指令失敗(請確認 bot 是用含 applications.commands "
                    "scope 的網址邀請的)"
                )
        for guild in self.guilds:
            await self._sync_nickname(guild)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        await self._sync_nickname(guild)

    async def _sync_nickname(self, guild: discord.Guild) -> None:
        """把自己在這個伺服器的暱稱改成角色名;沒權限時只記警告。"""
        me = guild.me
        if me is None or self.user is None:
            return
        should_change, nick = nickname_change(me.nick, self.user.name, self._nickname)
        if not should_change:
            return
        try:
            await me.edit(nick=nick)
            logger.info("已把伺服器 %s 的暱稱改成 %s", guild.name, nick or self.user.name)
        except discord.Forbidden:
            logger.warning("伺服器 %s 沒有給「變更暱稱」權限,暱稱維持不變", guild.name)
        except discord.HTTPException:
            logger.exception("修改伺服器 %s 的暱稱失敗", guild.name)

    def _register_commands(self) -> None:
        """註冊伺服器的狀態查詢與管理指令。"""
        @self.tree.command(name="llmstatus", description="查看模型與本頻道上下文狀態")
        @app_commands.guild_only()
        async def llmstatus(interaction: discord.Interaction) -> None:
            if interaction.channel_id is None:
                await interaction.response.send_message(
                    "目前無法辨識這個頻道。", ephemeral=True
                )
                return
            status = self._pipeline.llm_status(interaction.channel_id)
            await interaction.response.send_message(
                embed=_llm_status_embed(status),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @self.tree.command(name="llmclear", description="清空漿糖在本頻道的短期記憶")
        @app_commands.guild_only()
        @app_commands.default_permissions(manage_messages=True)
        async def llmclear(interaction: discord.Interaction) -> None:
            if interaction.channel_id is None:
                text = "目前無法辨識這個頻道。"
            elif not interaction.permissions.manage_messages:
                text = "清除本頻道的記憶需要「管理訊息」權限。"
            else:
                count = self._pipeline.clear_context(interaction.channel_id)
                self._turns.finish(interaction.channel_id)
                text = f"已清除本頻道的短期記憶（{count} 則）。"
            await interaction.response.send_message(text, ephemeral=True)

        group = app_commands.Group(
            name="set",
            description="設定漿糖可以互動的頻道",
            guild_only=True,
            default_permissions=discord.Permissions(manage_guild=True),
        )

        @group.command(name="allow", description="把指定頻道加入允許清單")
        @app_commands.describe(channel="要加入的文字頻道")
        async def allow_channel(
            interaction: discord.Interaction, channel: discord.TextChannel
        ) -> None:
            self._policy.allow(channel.id)
            await interaction.response.send_message(
                f"好喔～我可以在 {channel.mention} 互動了 🥰", ephemeral=True
            )

        @group.command(name="remove", description="把指定頻道移出允許清單")
        @app_commands.describe(channel="要移出的文字頻道")
        async def remove_channel(
            interaction: discord.Interaction, channel: discord.TextChannel
        ) -> None:
            self._policy.disallow(channel.id)
            if self._policy.restricted:
                text = f"已將 {channel.mention} 移出允許清單。"
            else:
                text = "目前允許清單是空的，我可以在所有看得到的頻道互動喔。"
            await interaction.response.send_message(text, ephemeral=True)

        @group.command(name="channel", description="讓漿糖只在指定頻道互動")
        @app_commands.describe(channel="唯一允許的文字頻道")
        async def set_channel(
            interaction: discord.Interaction, channel: discord.TextChannel
        ) -> None:
            self._policy.only(channel.id)
            await interaction.response.send_message(
                f"好～我只會在 {channel.mention} 回話和貼表情。", ephemeral=True
            )

        @group.command(name="all", description="解除互動的頻道限制")
        async def allow_all_channels(interaction: discord.Interaction) -> None:
            self._policy.clear()
            await interaction.response.send_message(
                "好～我在所有看得到的頻道都可以互動了。", ephemeral=True
            )

        @group.command(name="status", description="查看漿糖目前允許互動的頻道")
        async def channel_status(interaction: discord.Interaction) -> None:
            ids = self._policy.allowed_ids()
            if not ids:
                text = "目前所有看得到的頻道都允許互動。"
            else:
                mentions = "、".join(f"<#{cid}>" for cid in sorted(ids))
                text = f"目前只會在這些頻道互動：{mentions}"
            await interaction.response.send_message(text, ephemeral=True)

        self.tree.add_command(group)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            # 別的 bot 的話只記進上下文讓漿糖「看得到」,不觸發回覆或表情,
            # 避免兩個 bot 互相接話停不下來。自己的訊息由送出流程記錄。
            if self.user is None or message.author.id != self.user.id:
                self._pipeline.observe(self._to_incoming(message))
            return

        incoming = self._to_incoming(message)
        self._schedule_lull(message.channel, incoming)
        turn = self._turns.submit(
            incoming, self._pipeline.context_revision(incoming.channel_id),
            merge=self._pipeline.can_merge(incoming),
        )
        reaction = self._pipeline.reaction_for(incoming)
        if turn is None:
            if reaction is not None:
                await self._react(message, reaction)
            return

        # 讓回合先在當前事件排隊，表情可以獨立送，避免下一個回合超車。
        reaction_task = (
            asyncio.create_task(self._react(message, reaction)) if reaction else None
        )
        try:
            await self._process_turn(message.channel, turn)
        finally:
            if reaction_task is not None:
                await reaction_task

    async def _process_turn(
        self, channel: discord.abc.Messageable, turn: PendingTurn
    ) -> None:
        """同一頻道依序規劃並送出回覆，其他頻道可獨立處理。"""
        channel_id = turn.messages[0].channel_id
        lock = self._turn_locks.setdefault(channel_id, asyncio.Lock())
        try:
            async with lock:
                messages = await self._turns.collect(turn)
                result = await self._pipeline.handle_turn(messages, turn.revision)
                if result.replies or result.gif is not None:
                    await self._send_replies(channel, messages[-1], result)
        except Exception:  # noqa: BLE001 — 單一回合失敗不影響後續回合。
            logger.exception("處理頻道 %s 的對話回合時發生錯誤", channel_id)

    def _schedule_lull(self, channel: discord.abc.Messageable, msg: IncomingMessage) -> None:
        """有人講話就重設冷場計時;安靜夠久時給角色一次開口的機會。"""
        if (waiting := self._lull_waits.pop(msg.channel_id, None)) is not None:
            waiting.cancel()
        delay = self._pipeline.note_activity(msg)
        if delay is None:
            return
        revision = self._pipeline.context_revision(msg.channel_id)
        self._lull_waits[msg.channel_id] = asyncio.create_task(
            self._wait_for_lull(channel, msg.channel_id, delay, revision)
        )

    async def _wait_for_lull(
        self, channel: discord.abc.Messageable, channel_id: int, delay: float, revision: int
    ) -> None:
        await asyncio.sleep(delay)
        # 睡醒後就不再被新訊息取消,避免送到一半被中斷。
        if self._lull_waits.get(channel_id) is asyncio.current_task():
            del self._lull_waits[channel_id]
        lock = self._turn_locks.setdefault(channel_id, asyncio.Lock())
        try:
            async with lock:
                result = await self._pipeline.handle_lull(channel_id, revision)
                if result.replies:
                    await self._send_replies(channel, lull_message(channel_id), result)
        except Exception:  # noqa: BLE001 — 冷場開口失敗不影響其他流程。
            logger.exception("頻道 %s 冷場開口時發生錯誤", channel_id)

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
        is_reply_to_bot = self._is_reply_to_bot(message)
        return IncomingMessage(
            channel_id=message.channel.id,
            author=message.author.display_name,
            author_id=message.author.id,
            content=self._clean_content(message),
            author_is_bot=message.author.bot,
            mentions_bot=self.user in message.mentions,
            is_reply_to_bot=is_reply_to_bot,
            addresses_others=(
                (message.type is discord.MessageType.reply and not is_reply_to_bot)
                or any(user != self.user for user in message.mentions)
                or bool(message.role_mentions)
                or message.mention_everyone
            ),
        )

    def _clean_content(self, message: discord.Message) -> str:
        """移除訊息中對自己的 @ 提及,取出實際文字。"""
        content = message.content
        for mention in (f"<@{self.user.id}>", f"<@!{self.user.id}>"):
            content = content.replace(mention, "")
        if message.author.bot:
            # 很多 bot 只用 embed 說話,把標題與內文也攤平成文字。
            parts = [content.strip()]
            for embed in message.embeds:
                parts.extend(
                    text.strip() for text in (embed.title, embed.description) if text
                )
            content = "\n".join(part for part in parts if part)
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

    async def _send_replies(
        self, channel: discord.abc.Messageable, msg: IncomingMessage, result: PipelineResult
    ) -> None:
        """保留打字停頓，並在每一段送出前確認記憶仍有效。"""
        if not self._pipeline.is_current_reply(msg.channel_id, result):
            return
        if result.replies:
            delay = min(
                max(len(result.replies[0]) * _TYPING_SECONDS_PER_CHAR, _TYPING_MIN_SECONDS),
                _TYPING_MAX_SECONDS,
            )
            async with channel.typing():
                await asyncio.sleep(delay)
        sent: list[str] = []
        gif_sent = False
        try:
            for chunk in result.replies:
                if sent:
                    await asyncio.sleep(_NEXT_CHUNK_DELAY)
                if not self._pipeline.is_current_reply(msg.channel_id, result):
                    return
                await channel.send(chunk)
                sent.append(chunk)

            if result.gif is not None:
                if sent:
                    await asyncio.sleep(_NEXT_CHUNK_DELAY)
                if not self._pipeline.is_current_reply(msg.channel_id, result):
                    return
                await channel.send(result.gif.url)
                gif_sent = True
        finally:
            self._pipeline.record_delivered_reply(msg, result, sent, gif_sent=gif_sent)
