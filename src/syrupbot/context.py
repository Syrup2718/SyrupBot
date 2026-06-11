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


class ConversationStore:
    """以頻道 ID 為鍵,保存有長度上限的近期訊息。"""

    def __init__(self, max_messages: int) -> None:
        self._max_messages = max_messages
        self._channels: dict[int, deque[ContextMessage]] = defaultdict(
            lambda: deque(maxlen=max_messages)
        )

    def add(self, channel_id: int, author: str, content: str, is_self: bool) -> None:
        """新增一則訊息;超過上限時自動丟棄最舊的。"""
        self._channels[channel_id].append(
            ContextMessage(author=author, content=content, is_self=is_self)
        )

    def messages(self, channel_id: int) -> list[ContextMessage]:
        """回傳某頻道目前的上下文(依時間排序)。"""
        return list(self._channels[channel_id])

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

    def clear(self, channel_id: int) -> None:
        """清除某頻道的上下文。"""
        self._channels.pop(channel_id, None)
