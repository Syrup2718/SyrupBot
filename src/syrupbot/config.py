"""集中管理執行期設定,全部從環境變數載入。

所有設定在啟動時讀取一次並做基本驗證,讓設定錯誤盡早失敗,
而不是在第一次處理訊息時才爆出來。
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_TURN_WINDOW_SECONDS = 0.8

# 優先載入本機 LLM 設定，再由 .env 補齊；程序環境變數優先於兩個檔案。
load_dotenv(Path(__file__).resolve().parents[2] / "llm.local.env")
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
    decision_strategy: str  # "heuristic"、"llm" 或 "hybrid"
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
    self_reaction_probability: float = 0.2  # 看到訊息時,自己貼表情的機率
    follow_reaction_probability: float = 0.5  # 別人按了表情時,跟著按的機率
    follow_reaction_min_count: int = 2  # 至少要有幾個人按過,才考慮跟著按
    disable_thinking: bool = False
    llama_server_exe: str | None = None
    llama_server_args: tuple[str, ...] = ()
    turn_window_seconds: float = DEFAULT_TURN_WINDOW_SECONDS
    gifs_enabled: bool = True
    gif_reply_probability: float = 0.12
    gif_cooldown_seconds: float = 90.0
    # hybrid 策略:先用 heuristic 分數決定「要不要看」,過了才讓 LLM 判斷。
    look_probability: float = 0.3
    # 話量疲勞的半衰期(秒):講越多越安靜,隨時間恢復;0 代表關閉。
    talk_fatigue_half_life_seconds: float = 300.0
    # 冷場接話:安靜太久時,開口的機率(0 關閉)與等待秒數的上下限。
    lull_probability: float = 0.1
    lull_min_seconds: float = 120.0
    lull_max_seconds: float = 1800.0

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
        if strategy not in ("heuristic", "llm", "hybrid"):
            raise RuntimeError(
                "DECISION_STRATEGY 必須是 'heuristic'、'llm' 或 'hybrid',"
                f"收到:{strategy!r}"
            )

        llama_exe = os.environ.get("LLAMA_SERVER_EXE") or None
        llama_args: tuple[str, ...] = ()
        if llama_exe:
            try:
                args = json.loads(os.environ.get("LLAMA_SERVER_ARGS", "[]"))
            except json.JSONDecodeError as exc:
                raise RuntimeError("LLAMA_SERVER_ARGS 必須是 JSON 字串陣列") from exc
            if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
                raise RuntimeError("LLAMA_SERVER_ARGS 必須是 JSON 字串陣列")
            llama_args = tuple(args)

        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
        turn_window = float(
            os.environ.get("TURN_WINDOW_SECONDS", str(DEFAULT_TURN_WINDOW_SECONDS))
        )
        if not math.isfinite(turn_window) or turn_window < 0:
            raise ValueError("TURN_WINDOW_SECONDS 必須是有限且非負的秒數")
        gif_probability = float(os.environ.get("GIF_REPLY_PROBABILITY", "0.12"))
        if not 0 <= gif_probability <= 1:
            raise ValueError("GIF_REPLY_PROBABILITY 必須介於 0 和 1")
        gif_cooldown = float(os.environ.get("GIF_COOLDOWN_SECONDS", "90"))
        if not math.isfinite(gif_cooldown) or gif_cooldown < 0:
            raise ValueError("GIF_COOLDOWN_SECONDS 必須是有限且非負的秒數")
        look_probability = float(os.environ.get("LOOK_PROBABILITY", "0.3"))
        if not 0 <= look_probability <= 1:
            raise ValueError("LOOK_PROBABILITY 必須介於 0 和 1")
        fatigue_half_life = float(os.environ.get("TALK_FATIGUE_HALF_LIFE_SECONDS", "300"))
        if not math.isfinite(fatigue_half_life) or fatigue_half_life < 0:
            raise ValueError("TALK_FATIGUE_HALF_LIFE_SECONDS 必須是有限且非負的秒數")
        lull_probability = float(os.environ.get("LULL_PROBABILITY", "0.1"))
        if not 0 <= lull_probability <= 1:
            raise ValueError("LULL_PROBABILITY 必須介於 0 和 1")
        lull_min = float(os.environ.get("LULL_MIN_SECONDS", "120"))
        lull_max = float(os.environ.get("LULL_MAX_SECONDS", "1800"))
        if not (math.isfinite(lull_min) and math.isfinite(lull_max) and 0 < lull_min <= lull_max):
            raise ValueError("LULL_MIN_SECONDS 與 LULL_MAX_SECONDS 必須為正數且下限不大於上限")
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
                os.environ.get("SELF_REACTION_PROBABILITY", "0.2")
            ),
            follow_reaction_probability=float(
                os.environ.get("FOLLOW_REACTION_PROBABILITY", "0.5")
            ),
            follow_reaction_min_count=int(
                os.environ.get("FOLLOW_REACTION_MIN_COUNT", "2")
            ),
            disable_thinking=_parse_bool(os.environ.get("LLM_DISABLE_THINKING", "false")),
            llama_server_exe=llama_exe,
            llama_server_args=llama_args,
            turn_window_seconds=turn_window,
            gifs_enabled=_parse_bool(os.environ.get("GIFS_ENABLED", "true")),
            gif_reply_probability=gif_probability,
            gif_cooldown_seconds=gif_cooldown,
            look_probability=look_probability,
            talk_fatigue_half_life_seconds=fatigue_half_life,
            lull_probability=lull_probability,
            lull_min_seconds=lull_min,
            lull_max_seconds=lull_max,
        )
