from syrupbot.postprocess import DISCORD_MAX_LEN, humanize, split_message


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
