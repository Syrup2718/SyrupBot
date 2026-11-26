from syrupbot.context import ConversationStore


def test_starts_empty() -> None:
    store = ConversationStore(max_messages=10)
    assert store.messages(1) == []
    assert store.transcript(1, "Syrup") == ""


def test_add_and_transcript_uses_self_name() -> None:
    store = ConversationStore(max_messages=10)
    store.add(1, "小明", "嗨", is_self=False)
    store.add(1, "Syrup", "嗨窩", is_self=True)

    assert store.transcript(1, "Syrup") == "小明:嗨\nSyrup:嗨窩"


def test_history_is_bounded() -> None:
    store = ConversationStore(max_messages=2)
    store.add(1, "a", "1", is_self=False)
    store.add(1, "b", "2", is_self=False)
    store.add(1, "c", "3", is_self=False)  # 應擠掉最舊的

    msgs = store.messages(1)
    assert [m.author for m in msgs] == ["b", "c"]


def test_channels_are_isolated() -> None:
    store = ConversationStore(max_messages=10)
    store.add(1, "甲", "頻道一", is_self=False)
    store.add(2, "乙", "頻道二", is_self=False)

    assert [m.content for m in store.messages(1)] == ["頻道一"]
    assert [m.content for m in store.messages(2)] == ["頻道二"]


def test_has_recent_self() -> None:
    store = ConversationStore(max_messages=10)
    store.add(1, "小明", "在嗎", is_self=False)
    assert store.has_recent_self(1) is False

    store.add(1, "Syrup", "在窩", is_self=True)
    assert store.has_recent_self(1) is True


def test_recent_self_falls_out_of_lookback() -> None:
    store = ConversationStore(max_messages=10)
    store.add(1, "Syrup", "嗨", is_self=True)
    for i in range(5):
        store.add(1, "別人", f"訊息{i}", is_self=False)
    assert store.has_recent_self(1, lookback=4) is False


def test_upsert_updates_by_message_id_or_appends():
    from syrupbot.context import ConversationStore

    store = ConversationStore(5)
    assert store.upsert(1, 10, "bot", "思考中") is False
    store.add(1, "小明", "嗨", is_self=False)
    assert store.upsert(1, 10, "bot", "答案是 42") is True
    assert [m.content for m in store.messages(1)] == ["答案是 42", "嗨"]
    assert store.channel_ids() == [1]


def test_speakers_maps_recent_names_to_ids_without_self():
    from syrupbot.context import ConversationStore

    store = ConversationStore(5)
    store.add(1, "小明", "嗨", is_self=False, author_id=7)
    store.add(1, "漿糖", "嗨嗨", is_self=True)
    store.add(1, "路人", "沒有 ID", is_self=False)
    store.upsert(1, 99, "音樂bot", "播放中", author_id=8)
    assert store.speakers(1) == {"小明": 7, "音樂bot": 8}
