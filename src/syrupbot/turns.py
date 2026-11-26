"""收集同一頻道、同一成員連發的訊息，等待時間依情境調整。

參考 MaiBot 的訊息防抖：
- 同一人每多打一則，就再等一個窗口（滑動），但整個回合有上限，不會無限等。
- 頻道很熱絡時窗口減半，才跟得上大家的節奏。
- 回合裡有人 @ 她或回覆她時，上限縮短，別讓對方等太久。
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from .decision import IncomingMessage

_MAX_TURN_MESSAGES = 4
# 整個回合最多等幾個基本窗口；被 @ 時改用較短的上限。
_TURN_CAP_WINDOWS = 3.0
_DIRECT_CAP_WINDOWS = 1.5
# 最近這段時間內有幾則訊息就算「熱絡」。
_BUSY_SPAN_SECONDS = 10.0
_BUSY_MESSAGE_COUNT = 4
_BUSY_FACTOR = 0.5


@dataclass(eq=False)
class PendingTurn:
    revision: int
    deadline: float
    messages: list[IncomingMessage]
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    started: float = 0.0
    window: float = 0.0


class TurnScheduler:
    """每個頻道各有一個可以接收連發訊息的回合。"""

    def __init__(
        self, window_seconds: float, *, clock: Callable[[], float] | None = None
    ) -> None:
        self._window = window_seconds
        self._clock = clock
        self._pending: dict[int, PendingTurn] = {}
        self._recent: dict[int, deque[float]] = {}

    def submit(
        self, msg: IncomingMessage, revision: int, *, merge: bool
    ) -> PendingTurn | None:
        """第一則取得回合；合併的後續訊息交給該回合一起處理。"""
        now = self._now()
        busy = self._note_activity(msg.channel_id, now)
        pending = self._pending.get(msg.channel_id)
        merge = merge and self._window > 0 and msg.author_id is not None
        if (
            merge
            and pending is not None
            and pending.revision == revision
            and pending.messages[0].author_id == msg.author_id
            and now < pending.deadline
        ):
            pending.messages.append(msg)
            if len(pending.messages) >= _MAX_TURN_MESSAGES:
                self.finish(msg.channel_id)
            else:
                pending.deadline = self._deadline(pending, now)
            return None

        self.finish(msg.channel_id)
        window = self._window * (_BUSY_FACTOR if busy else 1.0)
        turn = PendingTurn(revision, now, [msg], started=now, window=window)
        if merge:
            turn.deadline = self._deadline(turn, now)
            self._pending[msg.channel_id] = turn
        return turn

    def finish(self, channel_id: int) -> None:
        if turn := self._pending.pop(channel_id, None):
            turn.ready.set()

    async def collect(self, turn: PendingTurn) -> tuple[IncomingMessage, ...]:
        channel_id = turn.messages[0].channel_id
        loop = asyncio.get_running_loop()
        try:
            # 截止時間可能因續發而延後，所以逾時後要再檢查一次。
            while not turn.ready.is_set():
                remaining = turn.deadline - self._now()
                if remaining <= 0:
                    break
                try:
                    async with asyncio.timeout_at(loop.time() + remaining):
                        await turn.ready.wait()
                except TimeoutError:
                    continue
        finally:
            if self._pending.get(channel_id) is turn:
                self.finish(channel_id)
        return tuple(turn.messages)

    def _deadline(self, turn: PendingTurn, now: float) -> float:
        direct = any(m.mentions_bot or m.is_reply_to_bot for m in turn.messages)
        cap = self._window * (_DIRECT_CAP_WINDOWS if direct else _TURN_CAP_WINDOWS)
        return min(now + turn.window, turn.started + cap)

    def _note_activity(self, channel_id: int, now: float) -> bool:
        """記下這則訊息的時間，回傳頻道最近是否很熱絡。"""
        recent = self._recent.setdefault(channel_id, deque())
        recent.append(now)
        while recent and now - recent[0] > _BUSY_SPAN_SECONDS:
            recent.popleft()
        return len(recent) >= _BUSY_MESSAGE_COUNT

    def _now(self) -> float:
        if self._clock is not None:
            return self._clock()
        return asyncio.get_running_loop().time()


def combine_turn(messages: tuple[IncomingMessage, ...]) -> IncomingMessage:
    """保留這個回合內的 @ 與直接回覆，讓最新幾句一起成為回覆目標。"""
    first, *rest = messages
    if not rest:
        return first
    return replace(
        rest[-1],
        content="\n".join(msg.content for msg in messages if msg.content),
        mentions_bot=any(msg.mentions_bot for msg in messages),
        is_reply_to_bot=any(msg.is_reply_to_bot for msg in messages),
    )
