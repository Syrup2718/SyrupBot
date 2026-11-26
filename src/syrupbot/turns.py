"""在固定時間內收集同一頻道、同一成員連發的訊息。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace

from .decision import IncomingMessage


@dataclass(eq=False)
class PendingTurn:
    revision: int
    deadline: float
    messages: list[IncomingMessage]
    ready: asyncio.Event = field(default_factory=asyncio.Event)


class TurnScheduler:
    """每個頻道各有一個可以接收連發訊息的回合。"""

    def __init__(self, window_seconds: float) -> None:
        self._window = window_seconds
        self._pending: dict[int, PendingTurn] = {}

    def submit(
        self, msg: IncomingMessage, revision: int, *, merge: bool
    ) -> PendingTurn | None:
        """第一則取得回合；合併的後續訊息交給該回合一起處理。"""
        now = asyncio.get_running_loop().time()
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
            if len(pending.messages) >= 4:
                self.finish(msg.channel_id)
            return None

        self.finish(msg.channel_id)
        turn = PendingTurn(revision, now + self._window if merge else now, [msg])
        if merge:
            self._pending[msg.channel_id] = turn
        return turn

    def finish(self, channel_id: int) -> None:
        if turn := self._pending.pop(channel_id, None):
            turn.ready.set()

    async def collect(self, turn: PendingTurn) -> tuple[IncomingMessage, ...]:
        channel_id = turn.messages[0].channel_id
        try:
            if not turn.ready.is_set() and turn.deadline > asyncio.get_running_loop().time():
                async with asyncio.timeout_at(turn.deadline):
                    await turn.ready.wait()
        except TimeoutError:
            pass
        finally:
            if self._pending.get(channel_id) is turn:
                self.finish(channel_id)
        return tuple(turn.messages)


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
