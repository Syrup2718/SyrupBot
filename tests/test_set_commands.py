"""驗證 /set 子指令與現有頻道政策存檔相容。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from syrupbot.bot import SyrupBot
from syrupbot.channels import ChannelPolicy


async def test_set_commands_use_existing_channel_policy(tmp_path):
    path = tmp_path / "channels.json"
    ChannelPolicy(path=path).only(10)
    policy = ChannelPolicy(path=path)
    bot = SyrupBot(Mock(), policy)
    guild = discord.Object(id=123)
    bot.tree.copy_global_to(guild=guild)
    group = bot.tree.get_command("set", guild=guild)
    assert group.default_permissions.manage_guild
    assert bot.tree.get_command("漿糖", guild=guild) is None
    assert {command.name for command in group.commands} == {
        "channel", "allow", "remove", "all", "status",
    }

    send = AsyncMock()
    interaction = SimpleNamespace(response=SimpleNamespace(send_message=send))
    channel = SimpleNamespace(id=20, mention="<#20>")
    existing = SimpleNamespace(id=10, mention="<#10>")

    await group.get_command("status").callback(interaction)
    assert "<#10>" in send.await_args.args[0]
    only = group.get_command("channel")
    assert [parameter.name for parameter in only.parameters] == ["channel"]
    await only.callback(interaction, channel=channel)
    assert ChannelPolicy(path=path).allowed_ids() == frozenset({20})
    await group.get_command("allow").callback(interaction, channel=existing)
    assert policy.allowed_ids() == frozenset({10, 20})
    await group.get_command("remove").callback(interaction, channel=channel)
    assert policy.allowed_ids() == frozenset({10})
    await group.get_command("all").callback(interaction)
    assert not ChannelPolicy(path=path).restricted
    assert all(call.kwargs["ephemeral"] is True for call in send.await_args_list)
