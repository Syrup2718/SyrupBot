"""以假的 API 回覆驗證狀態指令與頻道隔離。"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from openai.types.chat import ChatCompletion

from syrupbot import llm
from syrupbot.bot import SyrupBot
from syrupbot.channels import ChannelPolicy
from syrupbot.context import ConversationStore
from syrupbot.pipeline import ChatPipeline


@pytest.fixture
def setup_status(monkeypatch):
    create = AsyncMock()
    monkeypatch.setattr(
        llm, "AsyncOpenAI", lambda **_: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        ),
    )
    config = SimpleNamespace(
        model="qwen-chat", decision_model="judge-model", temperature=1.1,
        disable_thinking=False, deepseek_api_key="fake-key",
        deepseek_base_url="http://127.0.0.1:9932/v1",
    )
    client = llm.LLMClient(config)
    context = ConversationStore(20)
    pipeline = ChatPipeline(Mock(), context, Mock(), client, Mock())
    return client, context, pipeline, create


def _completion(content, **extra):
    return ChatCompletion.model_validate({
        "id": "test", "model": "test-model", "object": "chat.completion", "created": 0,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42},
        **extra,
    })


async def test_reply_and_judge_share_chat_guidance(setup_status):
    client, _, _, create = setup_status
    transcript = "小明:睡著了\n小明:在幹嘛"
    latest = ("小明", "在幹嘛")
    create.side_effect = [
        _completion("耍廢"),
        _completion('{"should_reply": true, "reply": "耍廢"}'),
    ]
    assert await client.generate_reply("人設", transcript, latest_message=latest) == "耍廢"
    assert await client.judge_and_reply("人設", transcript, latest_message=latest) == (
        True, "耍廢"
    )

    for call in create.await_args_list:
        system, user = call.kwargs["messages"]
        assert system["role"] == "system"
        assert "先回應最後一則成員訊息" in system["content"]
        assert "先直接回答，再簡單安撫" in system["content"]
        assert user["role"] == "user"
        assert json.loads(user["content"]) == {
            "近期群聊紀錄": transcript,
            "這次要回的訊息": {"發話者": "小明", "內容": "在幹嘛"},
        }


async def _status_embed(pipeline, channel_id):
    bot = SyrupBot(pipeline, ChannelPolicy())
    command = bot.tree.get_command("llmstatus")
    assert command is not None
    assert command.default_permissions is None
    send = AsyncMock()
    await command.callback(SimpleNamespace(channel_id=channel_id, response=SimpleNamespace(
        send_message=send,
    )))
    assert send.await_args.kwargs["ephemeral"] is True
    assert not send.await_args.args
    return send.await_args.kwargs["embed"].to_dict()


async def test_status_command_shows_native_timings_for_this_channel(setup_status):
    client, context, pipeline, create = setup_status
    context.add(7, "使用者", "群聊私訊測試內容", is_self=False)
    create.return_value = _completion(
        "在呀", timings={"prompt_per_second": 104.5, "predicted_per_second": 53.25}
    )
    assert await client.generate_reply("人設", "對話", channel_id=7) == "在呀"

    embed = await _status_embed(pipeline, 7)
    assert embed["title"] == "漿糖的模型狀態"
    fields = {field["name"]: field["value"] for field in embed["fields"]}
    assert fields["本頻道上下文"] == "**1 / 20** 則訊息"
    assert fields["Token 用量"] == "輸入 **30** · 輸出 **12** · 總計 **42**"
    assert fields["輸入處理速度"] == "**104.5** tok/s"
    assert fields["生成速度"] == "**53.2** tok/s"
    assert "群聊私訊測試內容" not in str(embed)

    other = await _status_embed(pipeline, 8)
    other_fields = {field["name"]: field["value"] for field in other["fields"]}
    assert other_fields["本頻道上下文"] == "**0 / 20** 則訊息"
    assert "還沒有完成" in other_fields["最近一次請求"]
    assert "Token 用量" not in other_fields
    create.assert_awaited_once()


async def test_status_labels_fallback_and_preserves_stats_on_failure(
    monkeypatch, setup_status
):
    client, _, pipeline, create = setup_status
    monkeypatch.setattr(llm, "monotonic", Mock(side_effect=[1.0, 3.0, 4.0]))
    create.side_effect = [
        _completion('{"should_reply": false, "reply": ""}'), RuntimeError("API error")
    ]
    assert await client.judge_and_reply("人設", "對話", channel_id=7) == (False, "")
    with pytest.raises(RuntimeError, match="API error"):
        await client.generate_reply("人設", "對話", channel_id=7)

    embed = await _status_embed(pipeline, 7)
    fields = {field["name"]: field["value"] for field in embed["fields"]}
    assert "判斷是否插話" in fields["最近一次請求"]
    assert fields["平均輸出速度"] == "**6.0** tok/s（含等候）"
    assert client.in_flight == 0
