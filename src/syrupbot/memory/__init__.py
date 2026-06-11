"""長期記憶的擴充點(第一階段尚未接入主流程)。

短期上下文在 context.py;這裡預留的是「跨重啟、跨對話」的長期記憶——
記住使用者的喜好、稱呼、互動習慣等。第一階段先定義介面與一個 no-op 實作,
之後可換成 JSON 檔、SQLite 或向量資料庫,上層程式不必更動。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MemoryStore(Protocol):
    """長期記憶的最小介面。"""

    def remember(self, user_id: int, fact: str) -> None:
        """記住關於某個使用者的一件事。"""
        ...

    def recall(self, user_id: int) -> list[str]:
        """取回關於某個使用者已知的事(供組 prompt 用)。"""
        ...


class NullMemory:
    """什麼都不記的預設實作,讓主流程現在就能依賴介面而非具體實作。"""

    def remember(self, user_id: int, fact: str) -> None:
        return None

    def recall(self, user_id: int) -> list[str]:
        return []
