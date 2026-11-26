"""冷場偵測：預測下一則訊息大概何時會來，太久沒人講話時給角色一次開口的機會。

參考 MaiBot 的「靜默查看」：用最近訊息的平均間隔推估節奏，安靜超過幾倍間隔
就算冷場。這裡只負責算時間，要不要真的開口交給決策引擎和模型。
不碰 Discord 和網路，時鐘可注入，方便測試。
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

from .config import Config

# 只看最近幾則訊息的間隔；太久以前的訊息不算這段對話。
_HISTORY = 8
_STALE_SECONDS = 600.0
# 這段對話至少要有幾則訊息，才算「有在聊」，值得在冷場時接話。
_MIN_MESSAGES = 3
# 安靜超過平均間隔的幾倍才算冷場。
_GAP_MULTIPLIER = 4.0


class LullWatcher:
    """記錄每個頻道的發言時間，算出多久沒人講話算冷場。"""

    def __init__(self, config: Config, *, clock: Callable[[], float] | None = None) -> None:
        self._config = config
        self._clock = clock or time.monotonic
        self._times: dict[int, deque[float]] = {}

    @property
    def enabled(self) -> bool:
        return self._config.lull_probability > 0

    def note_message(self, channel_id: int) -> None:
        """有成員講話時記下時間。"""
        times = self._times.setdefault(channel_id, deque(maxlen=_HISTORY))
        times.append(self._clock())

    def delay_for(self, channel_id: int) -> float | None:
        """從現在起安靜多久算冷場;這段對話太少或功能關閉時回傳 None。"""
        if not self.enabled:
            return None
        now = self._clock()
        recent = [t for t in self._times.get(channel_id, ()) if now - t <= _STALE_SECONDS]
        if len(recent) < _MIN_MESSAGES:
            return None
        gaps = [later - earlier for earlier, later in zip(recent, recent[1:])]
        average = sum(gaps) / len(gaps)
        delay = average * _GAP_MULTIPLIER
        return max(self._config.lull_min_seconds, min(delay, self._config.lull_max_seconds))

    def clear(self, channel_id: int) -> None:
        self._times.pop(channel_id, None)
