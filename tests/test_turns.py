"""驗證回合只合併同一頻道與成員，並尊重清除版本。"""

from dataclasses import replace

from syrupbot.decision import IncomingMessage
from syrupbot.turns import TurnScheduler

_MESSAGE = IncomingMessage(1, "甲", "你好", False, False, False, author_id=7)


async def test_other_speaker_finishes_turn_without_joining_other_channels():
    scheduler = TurnScheduler(10)
    first = scheduler.submit(_MESSAGE, 0, merge=True)
    other_channel = scheduler.submit(replace(_MESSAGE, channel_id=2), 0, merge=True)
    next_speaker = scheduler.submit(replace(_MESSAGE, author_id=8), 0, merge=True)
    assert first.ready.is_set()
    assert not other_channel.ready.is_set()
    assert not next_speaker.ready.is_set()
    assert await scheduler.collect(first) == (_MESSAGE,)


async def test_clear_revision_and_disabled_window_keep_turns_separate():
    scheduler = TurnScheduler(10)
    old = scheduler.submit(_MESSAGE, 0, merge=True)
    new = scheduler.submit(_MESSAGE, 1, merge=True)
    assert old.ready.is_set()
    assert new.revision == 1
    scheduler.finish(1)
    assert await scheduler.collect(old) == (_MESSAGE,)
    assert await scheduler.collect(new) == (_MESSAGE,)

    disabled = TurnScheduler(0)
    first = disabled.submit(_MESSAGE, 0, merge=True)
    second = disabled.submit(_MESSAGE, 0, merge=True)
    assert await disabled.collect(first) == await disabled.collect(second) == (_MESSAGE,)
