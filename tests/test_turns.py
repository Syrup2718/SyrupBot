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


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


async def test_follow_up_messages_slide_the_deadline_up_to_a_cap():
    clock = _Clock()
    scheduler = TurnScheduler(1.0, clock=clock)
    turn = scheduler.submit(_MESSAGE, 0, merge=True)
    assert turn.deadline == 101.0

    clock.now = 100.9
    assert scheduler.submit(_MESSAGE, 0, merge=True) is None
    assert turn.deadline == 101.9  # 續發延後一個窗口

    clock.now = 101.8
    scheduler.submit(_MESSAGE, 0, merge=True)
    assert turn.deadline == 102.8

    # 第四則直接結束回合
    clock.now = 102.5
    scheduler.submit(_MESSAGE, 0, merge=True)
    assert turn.ready.is_set()


async def test_deadline_never_exceeds_cap():
    clock = _Clock()
    scheduler = TurnScheduler(1.0, clock=clock)
    turn = scheduler.submit(_MESSAGE, 0, merge=True)
    clock.now = 100.95
    scheduler.submit(_MESSAGE, 0, merge=True)
    clock.now = 101.9
    scheduler.submit(_MESSAGE, 0, merge=True)
    assert turn.deadline == 102.9
    assert turn.deadline <= 100.0 + 3.0


async def test_mention_shortens_the_cap():
    clock = _Clock()
    scheduler = TurnScheduler(1.0, clock=clock)
    turn = scheduler.submit(replace(_MESSAGE, mentions_bot=True), 0, merge=True)
    clock.now = 100.9
    scheduler.submit(_MESSAGE, 0, merge=True)
    assert turn.deadline == 101.5  # 被 @ 時最多等 1.5 個窗口


async def test_busy_channel_halves_the_window():
    clock = _Clock()
    scheduler = TurnScheduler(1.0, clock=clock)
    for author in (1, 2, 3):
        scheduler.submit(replace(_MESSAGE, author_id=author), 0, merge=True)
        clock.now += 1
    turn = scheduler.submit(_MESSAGE, 0, merge=True)
    assert turn.deadline == clock.now + 0.5


async def test_collect_waits_for_extended_deadline():
    import asyncio

    scheduler = TurnScheduler(0.1)
    turn = scheduler.submit(_MESSAGE, 0, merge=True)
    waiter = asyncio.create_task(scheduler.collect(turn))
    await asyncio.sleep(0.07)
    second = replace(_MESSAGE, content="還有")
    scheduler.submit(second, 0, merge=True)
    assert await asyncio.wait_for(waiter, timeout=1) == (_MESSAGE, second)
