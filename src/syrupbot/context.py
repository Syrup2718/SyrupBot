"""每個頻道的近期聊天上下文。

群聊裡「誰說了什麼」很重要,所以每則訊息都記下發話者名字。內容存在記憶體,
重啟即清空——這是「短期上下文」,不是長期記憶(長期記憶見 memory/)。
要持久化時替換這個類別即可,介面不變。
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass(frozen=True)
class ContextMessage:
    """上下文中的一則訊息。"""

    author: str  # 發話者顯示名稱(角色自己的訊息用角色名)
    content: str
    is_self: bool  # 是否為角色自己說的話
    message_id: int | None = None  # Discord 訊息 ID,用來在訊息被編輯時更新內容


class ConversationStore:
    """以頻道 ID 為鍵,保存有長度上限的近期訊息。"""

    def __init__(self, max_messages: int) -> None:
        self._max_messages = max_messages
        self._revisions: dict[int, int] = {}
        self._channels: dict[int, deque[ContextMessage]] = defaultdict(
            lambda: deque(maxlen=max_messages)
        )

    def add(
        self, channel_id: int, author: str, content: str, is_self: bool,
        message_id: int | None = None,
    ) -> None:
        """新增一則訊息;超過上限時自動丟棄最舊的。"""
        self._channels[channel_id].append(
            ContextMessage(author, content, is_self, message_id)
        )

    def upsert(self, channel_id: int, message_id: int, author: str, content: str) -> bool:
        """依訊息 ID 更新內容(訊息被編輯時);找不到就新增。回傳是否為更新。"""
        history = self._channels[channel_id]
        for index, item in enumerate(history):
            if item.message_id == message_id:
                history[index] = ContextMessage(author, content, item.is_self, message_id)
                return True
        history.append(ContextMessage(author, content, False, message_id))
        return False

    def channel_ids(self) -> list[int]:
        """目前有上下文的頻道。"""
        return [cid for cid, items in self._channels.items() if items]

    def messages(self, channel_id: int) -> list[ContextMessage]:
        """回傳某頻道目前的上下文(依時間排序)。"""
        return list(self._channels[channel_id])

    def usage(self, channel_id: int) -> tuple[int, int]:
        """回傳頻道目前保留的訊息數及上限。"""
        return len(self._channels.get(channel_id, ())), self._max_messages

    def has_recent_self(self, channel_id: int, lookback: int = 4) -> bool:
        """角色最近是否參與了這串對話(用來判斷要不要延續話題)。"""
        recent = list(self._channels[channel_id])[-lookback:]
        return any(msg.is_self for msg in recent)

    def transcript(self, channel_id: int, self_name: str) -> str:
        """把上下文組成給 LLM 看的逐字稿。"""
        lines = []
        for msg in self._channels[channel_id]:
            speaker = self_name if msg.is_self else msg.author
            lines.append(f"{speaker}:{msg.content}")
        return "\n".join(lines)

    def revision(self, channel_id: int) -> int:
        """取得某頻道的清除版本，用來辨識清除前開始的回覆。"""
        return self._revisions.get(channel_id, 0)

    def clear(self, channel_id: int) -> int:
        """清除某頻道的上下文，並回傳移除的訊息數。"""
        self._revisions[channel_id] = self.revision(channel_id) + 1
        return len(self._channels.pop(channel_id, ()))
