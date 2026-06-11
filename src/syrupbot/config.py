"""集中管理執行期設定,全部從環境變數載入。

所有設定在啟動時讀取一次並做基本驗證,讓設定錯誤盡早失敗,
而不是在第一次處理訊息時才爆出來。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# 在讀取任何環境變數前先載入 .env(若存在)。
load_dotenv()


def _parse_channel_ids(raw: str) -> frozenset[int]:
    """把逗號分隔的頻道 ID 字串解析成集合;空字串代表不限制。"""
    if not raw.strip():
        return frozenset()
    return frozenset(int(part) for part in raw.split(",") if part.strip())


def _parse_bool(raw: str) -> bool:
    """把字串解析成布林;0/false/no/off/空字串視為 False,其餘 True。"""
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


@dataclass(frozen=True)
class Config:
    """所有執行期設定的單一來源。"""

    discord_token: str
    deepseek_api_key: str
    deepseek_base_url: str
    model: str
    decision_model: str
    temperature: float
    persona: str
    decision_strategy: str  # "heuristic" 或 "llm"
    reply_probability: float
    reply_cooldown_seconds: float
    decision_cooldown_seconds: float
    max_context_messages: int
    allowed_channel_ids: frozenset[int]
    log_level: str
    # 管理員指令即時調整頻道後,設定存到這個檔(重啟不忘)。
    channel_policy_path: str = "data/channels.json"
    # ── 表情符號回應(新欄位一律給預設值,避免既有的 Config(**...) 呼叫壞掉)──
    reactions_enabled: bool = True
    self_reaction_probability: float = 0.1  # 看到訊息時,自己貼表情的機率
    follow_reaction_probability: float = 0.3  # 別人按了表情時,跟著按的機率
    follow_reaction_min_count: int = 2  # 至少要有幾個人按過,才考慮跟著按

    @classmethod
    def from_env(cls) -> "Config":
        """從環境變數建立設定,缺少必填項目時拋出例外。"""
        missing = [
            name
            for name in ("DISCORD_TOKEN", "DEEPSEEK_API_KEY")
            if not os.environ.get(name)
        ]
        if missing:
            raise RuntimeError(
                f"缺少必要的環境變數:{', '.join(missing)}。"
                "請參考 .env.example 設定。"
            )

        strategy = os.environ.get("DECISION_STRATEGY", "heuristic").lower()
        if strategy not in ("heuristic", "llm"):
            raise RuntimeError(
                f"DECISION_STRATEGY 必須是 'heuristic' 或 'llm',收到:{strategy!r}"
            )

        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        return cls(
            discord_token=os.environ["DISCORD_TOKEN"],
            deepseek_api_key=os.environ["DEEPSEEK_API_KEY"],
            deepseek_base_url=os.environ.get(
                "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
            ),
            model=model,
            # 預留:之後可用較便宜的模型專做「該不該回」判斷。
            decision_model=os.environ.get("DECISION_MODEL", model),
            temperature=float(os.environ.get("LLM_TEMPERATURE", "1.1")),
            persona=os.environ.get("PERSONA", "syrup"),
            decision_strategy=strategy,
            reply_probability=float(os.environ.get("REPLY_PROBABILITY", "0.08")),
            reply_cooldown_seconds=float(
                os.environ.get("REPLY_COOLDOWN_SECONDS", "20")
            ),
            decision_cooldown_seconds=float(
                os.environ.get("DECISION_COOLDOWN_SECONDS", "10")
            ),
            max_context_messages=int(os.environ.get("MAX_CONTEXT_MESSAGES", "20")),
            allowed_channel_ids=_parse_channel_ids(
                os.environ.get("ALLOWED_CHANNEL_IDS", "")
            ),
            log_level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            channel_policy_path=os.environ.get(
                "CHANNEL_POLICY_PATH", "data/channels.json"
            ),
            reactions_enabled=_parse_bool(
                os.environ.get("REACTIONS_ENABLED", "true")
            ),
            self_reaction_probability=float(
                os.environ.get("SELF_REACTION_PROBABILITY", "0.1")
            ),
            follow_reaction_probability=float(
                os.environ.get("FOLLOW_REACTION_PROBABILITY", "0.3")
            ),
            follow_reaction_min_count=int(
                os.environ.get("FOLLOW_REACTION_MIN_COUNT", "2")
            ),
        )
