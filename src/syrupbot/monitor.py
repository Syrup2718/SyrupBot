"""後台用的事件紀錄:最近發生了什麼、為什麼回或不回。

只存在記憶體、有長度上限,重啟即清空。不碰 Discord 和網路,方便測試。
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass

# 事件種類(後台會依此上色與篩選)。
HUMAN = "human"  # 成員發言
BOT_SEEN = "bot_seen"  # 別的 bot 發言,已記進上下文
BOT_IGNORED = "bot_ignored"  # 別的 bot 發言,但沒記進上下文
DECISION = "decision"  # 回覆判斷結果
REPLY = "reply"  # 實際送出的回覆
LULL = "lull"  # 冷場接話


@dataclass(frozen=True)
class MonitorEvent:
    at: float  # Unix 時間(秒)
    kind: str
    channel_id: int
    author: str = ""
    text: str = ""
    detail: str = ""


class Monitor:
    """保存最近的事件,給後台顯示。"""

    def __init__(
        self, max_events: int = 300, *, clock: Callable[[], float] | None = None
    ) -> None:
        self._events: deque[MonitorEvent] = deque(maxlen=max_events)
        self._clock = clock or time.time

    def record(
        self, kind: str, channel_id: int, *, author: str = "", text: str = "",
        detail: str = "",
    ) -> None:
        self._events.append(
            MonitorEvent(self._clock(), kind, channel_id, author, text[:500], detail)
        )

    def events(self) -> list[dict]:
        """由新到舊的事件清單(可直接轉 JSON)。

        頻道 ID 轉成字串:Discord 的 ID 超過 JavaScript 能精確表示的整數範圍。
        """
        return [
            {**asdict(event), "channel_id": str(event.channel_id)}
            for event in reversed(self._events)
        ]
