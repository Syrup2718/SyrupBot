"""「漿糖可以在哪些頻道講話」的可變狀態(可由管理員指令即時調整)。

語意是**允許清單(allowlist)**:清單為空 = 所有頻道都可以(維持原本預設行為);
一旦清單有內容,就只在清單內的頻道講話與貼表情。

與 config 的差別:`ALLOWED_CHANNEL_IDS` 是啟動時的靜態初始值;這個類別讓管理員在
執行期即時改,並把結果存成 JSON 檔,重啟後不會忘。decision 與 reaction 都依賴同一個
實例,所以一次調整、處處生效。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ChannelPolicy:
    """哪些頻道允許漿糖互動;空集合代表「所有頻道」。"""

    def __init__(
        self,
        *,
        initial: frozenset[int] = frozenset(),
        path: Path | None = None,
    ) -> None:
        self._path = path
        self._allowed: set[int] = set(initial)
        # 有存檔就以存檔為準(讓執行期的調整覆蓋 .env 的初始值)。
        if path is not None and path.exists():
            self._load()

    def is_allowed(self, channel_id: int) -> bool:
        """這個頻道允不允許互動(空清單=全部允許)。"""
        return not self._allowed or channel_id in self._allowed

    def allow(self, channel_id: int) -> None:
        """把某頻道加進允許清單。"""
        self._allowed.add(channel_id)
        self._save()

    def disallow(self, channel_id: int) -> None:
        """把某頻道移出允許清單。"""
        self._allowed.discard(channel_id)
        self._save()

    def only(self, channel_id: int) -> None:
        """設成「只在這一個頻道」。"""
        self._allowed = {channel_id}
        self._save()

    def clear(self) -> None:
        """清空限制,回到「所有頻道都可以」。"""
        self._allowed.clear()
        self._save()

    def allowed_ids(self) -> frozenset[int]:
        """目前允許清單(空=所有頻道)。"""
        return frozenset(self._allowed)

    @property
    def restricted(self) -> bool:
        """是否有設限(False = 所有頻道都可以)。"""
        return bool(self._allowed)

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))  # type: ignore[union-attr]
            self._allowed = {int(x) for x in data.get("allowed", [])}
        except (OSError, ValueError):
            logger.warning("讀取頻道設定失敗,沿用初始值:%s", self._path, exc_info=True)

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"allowed": sorted(self._allowed)}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("寫入頻道設定失敗:%s", self._path, exc_info=True)
