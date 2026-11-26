from syrupbot.postprocess import (
    DISCORD_MAX_LEN,
    humanize,
    is_chat_replay,
    split_chat_reply,
    split_message,
)


def test_humanize_keeps_natural_reply() -> None:
    assert humanize("辛苦了餒 早點休息啦") == "辛苦了餒 早點休息啦"


def test_humanize_strips_filler_prefix() -> None:
    assert humanize("好的，我來幫你看看") == "我來幫你看看"


def test_humanize_strips_assistant_suffix() -> None:
    out = humanize("這個我知道。還有什麼我可以幫你的嗎？")
    assert "可以幫你" not in out
    assert out.startswith("這個我知道")


def test_humanize_removes_bullets_and_headings() -> None:
    text = "# 標題\n- 第一點\n- 第二點"
    out = humanize(text)
    assert "#" not in out
    assert "-" not in out
    assert "第一點" in out and "第二點" in out


def test_humanize_strips_wrapping_quotes() -> None:
    assert humanize("「真的假的啦」") == "真的假的啦"


def test_humanize_keeps_inner_quotes() -> None:
    # 內部有同款引號時不應剝除,避免破壞內容。
    text = "他說「好」我說「不要」"
    assert humanize(text) == text


def test_humanize_drops_action_asides() -> None:
    assert humanize("早安嘛～（頂呆毛）") == "早安嘛～"
    assert humanize("*歪頭*我在喔(眨眼)") == "我在喔"
    assert humanize("（摸摸星星髮夾）") == ""


def test_humanize_keeps_regular_asides_and_emphasis() -> None:
    text = "我月底才有空（週末除外），這首歌真的*很好聽*"
    assert humanize(text) == text


def test_chat_replay_matches_speaker_labels_but_preserves_live_chat() -> None:
    assert is_chat_replay("咦\n**漿糖**：我在啦", speakers={"漿糖"}, previous_replies=[])
    assert not is_chat_replay("好啊", speakers={"漿糖"}, previous_replies=["好啊"])
    assert not is_chat_replay("網址：https://example.org", speakers={"漿糖"}, previous_replies=[])


def test_split_short_message_unchanged() -> None:
    assert split_message("你好") == ["你好"]


def test_split_empty_returns_empty_list() -> None:
    assert split_message("") == []


def test_split_long_message_within_limit() -> None:
    text = "a" * (DISCORD_MAX_LEN * 2 + 50)
    chunks = split_message(text)
    assert len(chunks) >= 3
    assert all(len(c) <= DISCORD_MAX_LEN for c in chunks)


def test_split_prefers_newline_boundary() -> None:
    first = "x" * 1500
    second = "y" * 1000
    chunks = split_message(f"{first}\n{second}", limit=2000)
    assert chunks[0] == first
    assert chunks[1] == second


def test_chat_reply_keeps_short_messages_together() -> None:
    text = "好啊！你要玩什麼？"
    assert split_chat_reply(text) == [text]


def test_chat_reply_splits_at_sentence_or_newline() -> None:
    first = "我剛剛才想到我們上次玩的那關其實有一條很容易錯過的路。"
    second = "下次可以先往右邊繞過去，看到橋之後再回頭找其他人。"
    assert split_chat_reply(first + second) == [first, second]
    assert split_chat_reply(f"{first[:-1]}\n{second}") == [first[:-1], second]


def test_chat_reply_preserves_links_and_discord_limit() -> None:
    text = "這個我昨天找了好久，後來才發現你貼的那篇文章就有說明。"
    text += "你可以先從這篇看看：https://example.org/?q=game"
    assert split_chat_reply(text) == [text]
    assert all(len(chunk) <= DISCORD_MAX_LEN for chunk in split_chat_reply("啊" * 2500))


def test_link_mentions_prefers_longest_name_and_keeps_unknown():
    from syrupbot.postprocess import link_mentions

    members = {"小明": 1, "小明明": 2}
    assert link_mentions("@小明明 你呢 ＠小明 也來", members) == "<@2> 你呢 小明 也來"  # 一則最多一個 tag
    assert link_mentions("@路人 你好", members) == "路人 你好"
    assert link_mentions("寄信到 a@b.com", members) == "寄信到 a@b.com"


def test_unlink_mentions_round_trip():
    from syrupbot.postprocess import link_mentions, unlink_mentions

    members = {"小明": 123456789012345678}
    linked = link_mentions("@小明 快來", members)
    assert linked == "<@123456789012345678> 快來"
    assert unlink_mentions(linked, members) == "@小明 快來"
    assert unlink_mentions("<@!123456789012345678> 嗨 <@9>", members) == "@小明 嗨 <@9>"


def test_link_mentions_tolerates_spaces_and_skips_addressee():
    from syrupbot.postprocess import link_mentions

    members = {"酒鬼001": 1, "酒鬼004": 4}
    assert link_mentions("@酒鬼 001 你敢嗎", members) == "<@1> 你敢嗎"
    assert link_mentions("@酒鬼 004 你那不是有圖證嗎", members, skip_id=4) == "你那不是有圖證嗎"
    assert link_mentions("去問 @酒鬼004 啦", members, skip_id=4) == "去問 酒鬼004 啦"
    assert link_mentions("@酒鬼001 @酒鬼004 都來", members) == "<@1> 酒鬼004 都來"
