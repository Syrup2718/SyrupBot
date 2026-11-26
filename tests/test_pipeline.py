"""驗證回覆檢查及短期記憶清除。"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from syrupbot import bot as bot_module
from syrupbot.bot import SyrupBot
from syrupbot.channels import ChannelPolicy
from syrupbot.context import ConversationStore
from syrupbot.decision import IncomingMessage, ReplyDecision
from syrupbot.persona import Persona
from syrupbot.pipeline import ChatPipeline, PipelineResult

_OLD_REPLY = "哪有 誰叫你傻逼啦！\n是我剛才太笨把話忘光光嘛！\n那、那現在算帳囉～"
_REPLAY = f"{_OLD_REPLY}\n\n漿:我跳驗你\n漿糖：你隨便驗唄～\n漿:我討厭你"
_INCOMING = IncomingMessage(1, "漿", "你在說些什麼", False, False, False)


@pytest.fixture
def setup_pipeline():
    persona = Persona.load("syrup")
    context = ConversationStore(20)
    context.add(1, "漿糖", _OLD_REPLY, is_self=True)
    decision = Mock(decide=AsyncMock(return_value=ReplyDecision(True, "測試")))
    llm = Mock(generate_reply=AsyncMock())
    reaction = Mock(decide_self_reaction=Mock(return_value="😆"))
    return ChatPipeline(persona, context, decision, llm, reaction), context, decision, llm


async def test_replayed_transcript_retries_only_with_current_message(setup_pipeline):
    pipeline, context, decision, llm = setup_pipeline
    llm.generate_reply.side_effect = [_REPLAY, "我剛剛講歪了啦"]

    result = await pipeline.handle(_INCOMING)

    assert result.replies == ["我剛剛講歪了啦"]
    assert result.reaction == "😆"
    first, retry = llm.generate_reply.await_args_list
    assert _OLD_REPLY in first.args[1]
    assert retry.args[1] == ""
    assert retry.kwargs["latest_message"] == ("漿", "你在說些什麼")
    assert [item.content for item in context.messages(1)] == [_OLD_REPLY, "你在說些什麼"]
    decision.record_reply.assert_not_called()
    pipeline.record_delivered_reply(_INCOMING, result, result.replies)
    assert [item.content for item in context.messages(1)] == [
        _OLD_REPLY, "你在說些什麼", "我剛剛講歪了啦",
    ]
    decision.record_reply.assert_called_once_with(1)


async def test_repeated_reply_from_judge_is_dropped_if_retry_also_replays(setup_pipeline):
    pipeline, context, decision, llm = setup_pipeline
    decision.decide.return_value = ReplyDecision(True, "測試", reply=_OLD_REPLY)
    llm.generate_reply.return_value = _OLD_REPLY.replace("！", "。")

    result = await pipeline.handle(_INCOMING)

    assert result.replies == []
    assert result.reaction == "😆"
    llm.generate_reply.assert_awaited_once()
    assert len(context.messages(1)) == 2
    decision.record_reply.assert_not_called()


async def test_reworded_old_reply_also_retries(setup_pipeline):
    pipeline, _, _, llm = setup_pipeline
    llm.generate_reply.side_effect = [
        "沒有呀 誰叫你傻逼啦！那...那算我剛才太笨把話忘光光囉～",
        "我剛剛講歪了啦",
    ]
    result = await pipeline.handle(_INCOMING)
    assert result.replies == ["我剛剛講歪了啦"]
    assert llm.generate_reply.await_count == 2


async def test_llmclear_requires_permission_and_clears_only_that_channel(setup_pipeline):
    pipeline, context, decision, _ = setup_pipeline
    context.add(2, "乙", "另一個頻道", is_self=False)
    bot = SyrupBot(pipeline, ChannelPolicy())
    command = bot.tree.get_command("llmclear")
    assert command.default_permissions.manage_messages
    send = AsyncMock()
    interaction = SimpleNamespace(
        channel_id=1,
        permissions=SimpleNamespace(manage_messages=False),
        response=SimpleNamespace(send_message=send),
    )

    await command.callback(interaction)
    assert context.usage(1)[0] == 1
    send.assert_awaited_once_with("清除本頻道的記憶需要「管理訊息」權限。", ephemeral=True)

    interaction.permissions.manage_messages = True
    await command.callback(interaction)
    assert context.usage(1)[0] == 0
    assert context.usage(2)[0] == 1
    decision.clear_follow_up.assert_called_once_with(1)
    assert send.await_args.args == ("已清除本頻道的短期記憶（1 則）。",)
    assert send.await_args.kwargs == {"ephemeral": True}


async def test_clear_drops_in_flight_response_without_erasing_new_messages(setup_pipeline):
    pipeline, context, decision, llm = setup_pipeline

    async def generate(*args, **kwargs):
        pipeline.clear_context(1)
        context.add(1, "漿", "重新開始", is_self=False)
        return "清除前開始生成的舊回覆"

    llm.generate_reply.side_effect = generate
    result = await pipeline.handle(_INCOMING)

    assert result.replies == []
    assert [item.content for item in context.messages(1)] == ["重新開始"]
    decision.record_reply.assert_not_called()


async def test_clear_while_typing_stops_pending_send(setup_pipeline):
    pipeline, _, decision, llm = setup_pipeline
    llm.generate_reply.return_value = "舊回覆"
    result = await pipeline.handle(_INCOMING)
    bot = SyrupBot(pipeline, ChannelPolicy())

    @asynccontextmanager
    async def typing():
        pipeline.clear_context(1)
        yield

    channel = SimpleNamespace(typing=typing, send=AsyncMock())
    await bot._send_replies(channel, _INCOMING, result)

    channel.send.assert_not_awaited()
    decision.record_delivered_reply.assert_not_called()


async def test_discord_buffers_same_speaker_and_sends_one_reply(setup_pipeline, monkeypatch):
    pipeline, context, decision, llm = setup_pipeline
    decision.skip_reason.return_value = None
    llm.generate_reply.return_value = "好啊 你要玩什麼"
    pipeline.reaction_for = Mock(return_value=None)
    bot = SyrupBot(pipeline, ChannelPolicy(), turn_window_seconds=10)
    monkeypatch.setattr(bot, "_to_incoming", lambda message: message.incoming)
    monkeypatch.setattr(bot_module, "_TYPING_SECONDS_PER_CHAR", 0)
    monkeypatch.setattr(bot_module, "_TYPING_MIN_SECONDS", 0)

    @asynccontextmanager
    async def typing():
        yield

    channel = SimpleNamespace(id=1, typing=typing, send=AsyncMock())
    first = replace(_INCOMING, author_id=7, content="漿糖你在嗎", mentions_bot=True)
    second = replace(first, content="等等要打嗎", mentions_bot=False)
    messages = [SimpleNamespace(author=SimpleNamespace(bot=False), channel=channel,
                                incoming=msg) for msg in (first, second)]
    pending = asyncio.create_task(bot.on_message(messages[0]))
    await asyncio.sleep(0)
    await bot.on_message(messages[1])
    bot._turns.finish(1)
    await asyncio.wait_for(pending, timeout=1)

    decision.decide.assert_awaited_once()
    turn_msg = decision.decide.await_args.args[0]
    assert turn_msg.mentions_bot
    assert turn_msg.content == "漿糖你在嗎\n等等要打嗎"
    assert llm.generate_reply.await_args.kwargs["latest_message"] == (
        "漿", "漿糖你在嗎\n等等要打嗎",
    )
    channel.send.assert_awaited_once_with("好啊 你要玩什麼")
    assert [item.content for item in context.messages(1)][-3:] == [
        "漿糖你在嗎", "等等要打嗎", "好啊 你要玩什麼",
    ]


async def test_failed_second_chunk_records_only_first(setup_pipeline, monkeypatch):
    pipeline, context, decision, _ = setup_pipeline
    bot = SyrupBot(pipeline, ChannelPolicy())
    monkeypatch.setattr(bot_module, "_TYPING_SECONDS_PER_CHAR", 0)
    monkeypatch.setattr(bot_module, "_TYPING_MIN_SECONDS", 0)
    monkeypatch.setattr(bot_module, "_NEXT_CHUNK_DELAY", 0)

    @asynccontextmanager
    async def typing():
        yield

    result = bot_module.PipelineResult(replies=["第一句", "第二句"])
    channel = SimpleNamespace(typing=typing, send=AsyncMock(side_effect=[None, OSError()]))
    with pytest.raises(OSError):
        await bot._send_replies(channel, _INCOMING, result)

    assert [item.content for item in context.messages(1)] == [_OLD_REPLY, "第一句"]
    decision.record_reply.assert_called_once_with(1)


async def test_reaction_may_not_reorder_different_speakers(setup_pipeline, monkeypatch):
    pipeline, _, decision, llm = setup_pipeline
    decision.skip_reason.return_value = None
    llm.generate_reply.side_effect = ["回甲", "回乙"]
    pipeline.reaction_for = Mock(side_effect=["😆", None])
    bot = SyrupBot(pipeline, ChannelPolicy(), turn_window_seconds=10)
    monkeypatch.setattr(bot, "_to_incoming", lambda message: message.incoming)
    monkeypatch.setattr(bot, "_react", AsyncMock())
    monkeypatch.setattr(bot_module, "_TYPING_MIN_SECONDS", 0)
    monkeypatch.setattr(bot_module, "_TYPING_SECONDS_PER_CHAR", 0)

    @asynccontextmanager
    async def typing():
        yield

    channel = SimpleNamespace(id=1, typing=typing, send=AsyncMock())
    first = replace(_INCOMING, author="甲", author_id=1)
    second = replace(_INCOMING, author="乙", author_id=2)
    tasks = [
        asyncio.create_task(bot.on_message(SimpleNamespace(
            author=SimpleNamespace(bot=False), channel=channel, incoming=msg,
        )))
        for msg in (first, second)
    ]
    await asyncio.sleep(0)
    bot._turns.finish(1)
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=1)

    assert [call.args[0].author for call in decision.decide.await_args_list] == ["甲", "乙"]
    assert [call.args[0] for call in channel.send.await_args_list] == ["回甲", "回乙"]
    assert "漿糖:回甲" in llm.generate_reply.await_args_list[1].args[1]


async def test_clear_invalidates_a_turn_still_in_the_queue(setup_pipeline):
    pipeline, context, decision, _ = setup_pipeline
    revision = pipeline.context_revision(1)
    pipeline.clear_context(1)

    result = await pipeline.handle_turn((_INCOMING,), revision)

    assert not result.replies
    assert not context.messages(1)
    decision.decide.assert_not_awaited()


async def test_blocked_bot_messages_are_seen_but_not_answered(setup_pipeline):
    pipeline, context, decision, llm = setup_pipeline
    decision.channel_allowed.return_value = True
    decision.bot_block_reason.return_value = "設定為只看不回"
    bot = SyrupBot(pipeline, ChannelPolicy(), turn_window_seconds=10)
    bot._connection.user = SimpleNamespace(id=42)
    other = replace(_INCOMING, author="音樂bot", content="正在播放:晴天", author_is_bot=True)
    message = SimpleNamespace(author=SimpleNamespace(bot=True, id=99), incoming=other)
    bot._to_incoming = lambda m: m.incoming

    await bot.on_message(message)

    assert context.messages(1)[-1].author == "音樂bot"
    assert context.messages(1)[-1].content == "正在播放:晴天"
    decision.decide.assert_not_called()
    llm.generate_reply.assert_not_called()


def test_observe_skips_disallowed_channel(setup_pipeline):
    pipeline, context, decision, _ = setup_pipeline
    decision.channel_allowed.return_value = False
    before = len(context.messages(1))
    pipeline.observe(replace(_INCOMING, author_is_bot=True))
    assert len(context.messages(1)) == before


def test_nickname_change_rules():
    from syrupbot.bot import nickname_change

    assert nickname_change(None, "SyrupBot", "黑糖") == (True, "黑糖")
    assert nickname_change("黑糖", "SyrupBot", "黑糖") == (False, "黑糖")
    assert nickname_change("漿糖", "SyrupBot", "黑糖") == (True, "黑糖")
    assert nickname_change("漿糖", "漿糖", "漿糖") == (True, None)  # 同帳號名就清掉暱稱
    assert nickname_change(None, "漿糖", "漿糖") == (False, None)
    assert nickname_change("隨便", "SyrupBot", None) == (False, None)  # 關閉時不動
    assert nickname_change(None, "x", "糖" * 40)[1] == "糖" * 32


async def test_sync_nickname_edits_and_survives_missing_permission(setup_pipeline):
    import discord

    pipeline, *_ = setup_pipeline
    bot = SyrupBot(pipeline, ChannelPolicy(), nickname="黑糖")
    bot._connection.user = SimpleNamespace(id=42, name="SyrupBot")
    me = SimpleNamespace(nick=None, edit=AsyncMock())
    await bot._sync_nickname(SimpleNamespace(me=me, name="測試群"))
    me.edit.assert_awaited_once_with(nick="黑糖")

    denied = SimpleNamespace(
        nick=None,
        edit=AsyncMock(side_effect=discord.Forbidden(Mock(status=403, reason="x"), "no")),
    )
    await bot._sync_nickname(SimpleNamespace(me=denied, name="沒權限群"))  # 不應拋錯


async def test_answerable_bot_message_goes_through_reply_flow(setup_pipeline, monkeypatch):
    pipeline, context, decision, llm = setup_pipeline
    decision.bot_block_reason.return_value = None
    decision.skip_reason.return_value = None
    llm.generate_reply.return_value = "那首我也喜歡"
    pipeline.reaction_for = Mock(return_value=None)
    bot = SyrupBot(pipeline, ChannelPolicy(), turn_window_seconds=0)
    bot._connection.user = SimpleNamespace(id=42)
    monkeypatch.setattr(bot, "_to_incoming", lambda message: message.incoming)
    monkeypatch.setattr(bot_module, "_TYPING_SECONDS_PER_CHAR", 0)
    monkeypatch.setattr(bot_module, "_TYPING_MIN_SECONDS", 0)

    @asynccontextmanager
    async def typing():
        yield

    channel = SimpleNamespace(id=1, typing=typing, send=AsyncMock())
    other = replace(_INCOMING, author="音樂bot", content="正在播放:晴天",
                    author_is_bot=True, author_id=99)
    message = SimpleNamespace(author=SimpleNamespace(bot=True, id=99),
                              channel=channel, incoming=other)
    await bot.on_message(message)

    decision.decide.assert_awaited_once()
    channel.send.assert_awaited_once_with("那首我也喜歡")
    decision.record_bot_reply.assert_called_once_with(1)


async def test_first_chunk_quotes_target_when_someone_interrupted(setup_pipeline, monkeypatch):
    import discord

    pipeline, context, decision, _ = setup_pipeline
    decision.should_quote.return_value = True
    bot = SyrupBot(pipeline, ChannelPolicy())
    monkeypatch.setattr(bot_module, "_TYPING_SECONDS_PER_CHAR", 0)
    monkeypatch.setattr(bot_module, "_TYPING_MIN_SECONDS", 0)
    monkeypatch.setattr(bot_module, "_NEXT_CHUNK_DELAY", 0)

    @asynccontextmanager
    async def typing():
        yield

    channel = SimpleNamespace(id=1, typing=typing, send=AsyncMock())
    target = replace(_INCOMING, message_id=100)
    bot._note_latest(target)
    bot._note_latest(replace(_INCOMING, message_id=101))  # 有人插話
    result = PipelineResult(replies=["第一段", "第二段"], context_revision=0)
    await bot._send_replies(channel, target, result)

    assert decision.should_quote.call_args.kwargs == {"interrupted": True}
    first, second = channel.send.await_args_list
    reference = first.kwargs["reference"]
    assert isinstance(reference, discord.MessageReference)
    assert reference.message_id == 100
    assert first.kwargs["allowed_mentions"].replied_user is False
    assert second.args == ("第二段",) and "reference" not in second.kwargs


def test_lull_target_points_at_last_message_from_others(setup_pipeline):
    pipeline, context, _, _ = setup_pipeline
    context.add(1, "小明", "有人在嗎", is_self=False, message_id=77)
    assert pipeline.lull_target(1).message_id == 77
    context.add(1, "漿糖", "我在", is_self=True)
    assert pipeline.lull_target(1).message_id is None


async def test_reply_tags_recent_speaker_and_context_keeps_readable_name(setup_pipeline):
    pipeline, context, decision, llm = setup_pipeline
    decision.skip_reason.return_value = None
    llm.generate_reply.return_value = "@小華 你也來看"
    context.add(1, "小華", "我在", is_self=False, author_id=55)
    msg = replace(_INCOMING, author_id=7, content="大家看這個")

    result = await pipeline.handle_turn((msg,), 0)
    assert result.replies == ["<@55> 你也來看"]

    pipeline.record_delivered_reply(msg, result, result.replies)
    assert context.messages(1)[-1].content == "@小華 你也來看"


async def test_tagging_can_be_disabled(setup_pipeline):
    _, context, decision, llm = setup_pipeline
    persona = Persona.load("syrup")
    pipeline = ChatPipeline(persona, context, decision, llm, Mock(), tag_members=False)
    decision.skip_reason.return_value = None
    llm.generate_reply.return_value = "@小華 你也來看"
    context.add(1, "小華", "我在", is_self=False, author_id=55)
    result = await pipeline.handle_turn((replace(_INCOMING, author_id=7),), 0)
    assert result.replies == ["@小華 你也來看"]


def test_bot_never_pings_everyone_or_roles(setup_pipeline):
    pipeline, *_ = setup_pipeline
    mentions = SyrupBot(pipeline, ChannelPolicy()).allowed_mentions
    assert mentions.everyone is False and mentions.roles is False
    assert mentions.users is True


def test_incoming_tags_of_others_become_readable_names(setup_pipeline):
    pipeline, *_ = setup_pipeline
    bot = SyrupBot(pipeline, ChannelPolicy())
    me = SimpleNamespace(id=42)
    bot._connection.user = me
    friend = SimpleNamespace(id=55, display_name="小華")
    message = SimpleNamespace(
        content="<@42> 你看 <@55> 又遲到", mentions=[me, friend],
        author=SimpleNamespace(bot=False),
    )
    assert bot._clean_content(message) == "你看 @小華 又遲到"
