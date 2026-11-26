"""集中管理執行期設定。

設定分兩處放:
- ``settings.toml``(專案根目錄):所有可調的行為參數,分組並附說明,會進 git。
- ``.env`` / ``llm.local.env``:密鑰與模型連線設定(token、API key、模型名稱等),不進 git。

同一個參數若兩邊都有,環境變數優先(方便部署時臨時覆蓋),啟動時會記錄警告,
免得改了 settings.toml 卻沒生效還找不到原因。

所有設定在啟動時讀取一次並驗證,讓設定錯誤盡早失敗。
"""

from __future__ import annotations

import json
import math
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_TURN_WINDOW_SECONDS = 0.8
_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_PATH = _ROOT / "settings.toml"

# 優先載入本機 LLM 設定，再由 .env 補齊；程序環境變數優先於兩個檔案。
load_dotenv(_ROOT / "llm.local.env")
load_dotenv()


def _parse_channel_ids(raw: str) -> frozenset[int]:
    """把逗號分隔的頻道 ID 字串解析成集合;空字串代表不限制。"""
    if not raw.strip():
        return frozenset()
    return frozenset(int(part) for part in raw.split(",") if part.strip())


def _parse_bool(raw: str) -> bool:
    """把字串解析成布林;0/false/no/off/空字串視為 False,其餘 True。"""
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


# ── 設定項目表 ──────────────────────────────────────────────
# 每一項:Config 欄位、環境變數名、settings.toml 的 [區段] 與鍵、型別、預設值。


@dataclass(frozen=True)
class _Item:
    field: str
    env: str
    section: str
    key: str
    kind: str  # str / int / float / bool / prob / seconds / choice / channels
    default: object
    choices: tuple[str, ...] = ()


_ITEMS: tuple[_Item, ...] = (
    _Item("persona", "PERSONA", "persona", "name", "str", "syrup"),
    _Item("sync_nickname", "SYNC_NICKNAME", "persona", "sync_nickname", "bool", True),
    _Item("decision_strategy", "DECISION_STRATEGY", "reply", "strategy", "choice",
          "heuristic", ("heuristic", "llm", "hybrid")),
    _Item("reply_probability", "REPLY_PROBABILITY", "reply", "probability", "prob", 0.08),
    _Item("look_probability", "LOOK_PROBABILITY", "reply", "look_probability", "prob", 0.3),
    _Item("reply_cooldown_seconds", "REPLY_COOLDOWN_SECONDS", "reply",
          "cooldown_seconds", "seconds", 20.0),
    _Item("decision_cooldown_seconds", "DECISION_COOLDOWN_SECONDS", "reply",
          "decision_cooldown_seconds", "seconds", 10.0),
    _Item("talk_fatigue_half_life_seconds", "TALK_FATIGUE_HALF_LIFE_SECONDS", "reply",
          "fatigue_half_life_seconds", "seconds", 300.0),
    _Item("turn_window_seconds", "TURN_WINDOW_SECONDS", "reply", "turn_window_seconds",
          "seconds", DEFAULT_TURN_WINDOW_SECONDS),
    _Item("max_context_messages", "MAX_CONTEXT_MESSAGES", "reply", "max_context_messages",
          "int", 20),
    _Item("temperature", "LLM_TEMPERATURE", "reply", "temperature", "float", 1.1),
    _Item("lull_probability", "LULL_PROBABILITY", "lull", "probability", "prob", 0.1),
    _Item("lull_min_seconds", "LULL_MIN_SECONDS", "lull", "min_seconds", "seconds", 120.0),
    _Item("lull_max_seconds", "LULL_MAX_SECONDS", "lull", "max_seconds", "seconds", 1800.0),
    _Item("bot_reply_mode", "BOT_REPLY_MODE", "bots", "reply_mode", "choice", "all",
          ("off", "mention", "all")),
    _Item("bot_reply_max", "BOT_REPLY_MAX", "bots", "reply_max", "int", 3),
    _Item("bot_reply_window_seconds", "BOT_REPLY_WINDOW_SECONDS", "bots",
          "reply_window_seconds", "seconds", 600.0),
    _Item("reply_quote_mode", "REPLY_QUOTE_MODE", "quote", "mode", "choice", "auto",
          ("auto", "always", "off")),
    _Item("reply_quote_probability", "REPLY_QUOTE_PROBABILITY", "quote", "probability",
          "prob", 0.25),
    _Item("reply_quote_ping", "REPLY_QUOTE_PING", "quote", "ping", "bool", False),
    _Item("tag_members", "TAG_MEMBERS", "tag", "enabled", "bool", True),
    _Item("reactions_enabled", "REACTIONS_ENABLED", "reactions", "enabled", "bool", True),
    _Item("self_reaction_probability", "SELF_REACTION_PROBABILITY", "reactions",
          "self_probability", "prob", 0.2),
    _Item("follow_reaction_probability", "FOLLOW_REACTION_PROBABILITY", "reactions",
          "follow_probability", "prob", 0.5),
    _Item("follow_reaction_min_count", "FOLLOW_REACTION_MIN_COUNT", "reactions",
          "follow_min_count", "int", 2),
    _Item("gifs_enabled", "GIFS_ENABLED", "gifs", "enabled", "bool", True),
    _Item("gif_reply_probability", "GIF_REPLY_PROBABILITY", "gifs", "reply_probability",
          "prob", 0.12),
    _Item("gif_cooldown_seconds", "GIF_COOLDOWN_SECONDS", "gifs", "cooldown_seconds",
          "seconds", 90.0),
    _Item("allowed_channel_ids", "ALLOWED_CHANNEL_IDS", "channels", "allowed_ids",
          "channels", frozenset()),
    _Item("channel_policy_path", "CHANNEL_POLICY_PATH", "channels", "policy_path", "str",
          "data/channels.json"),
    _Item("dashboard_host", "DASHBOARD_HOST", "dashboard", "host", "str", "127.0.0.1"),
    _Item("dashboard_port", "DASHBOARD_PORT", "dashboard", "port", "int", 8787),
    _Item("log_level", "LOG_LEVEL", "log", "level", "str", "INFO"),
)


def _convert(item: _Item, raw: object, source: str) -> object:
    """把環境變數字串或 TOML 值轉成正確型別並驗證;不合法時拋出帶來源的錯誤。"""
    from_env = source == "環境變數"
    name = item.env if from_env else f"[{item.section}] {item.key}"
    where = f"{source} 的 {name}"
    try:
        if item.kind == "bool":
            value: object = _parse_bool(raw) if from_env else raw
            if not isinstance(value, bool):
                raise ValueError("要填 true 或 false")
        elif item.kind == "channels":
            if from_env:
                value = _parse_channel_ids(raw)
            elif isinstance(raw, list) and all(
                isinstance(x, int) or (isinstance(x, str) and x.isdigit()) for x in raw
            ):
                value = frozenset(int(x) for x in raw)
            else:
                raise ValueError("要填頻道 ID 的清單,例如 [123, 456]")
        elif item.kind == "int":
            if isinstance(raw, bool) or (not from_env and not isinstance(raw, int)):
                raise ValueError("要填整數")
            value = int(raw)
            if value < 0:
                raise ValueError("不能是負數")
        elif item.kind in ("float", "prob", "seconds"):
            if isinstance(raw, bool) or (not from_env and not isinstance(raw, int | float)):
                raise ValueError("要填數字")
            value = float(raw)
            if not math.isfinite(value):
                raise ValueError("要填有限的數字")
            if item.kind == "prob" and not 0 <= value <= 1:
                raise ValueError("機率要介於 0 和 1")
            if item.kind == "seconds" and value < 0:
                raise ValueError("秒數不能是負數")
        else:  # str / choice
            if not isinstance(raw, str):
                raise ValueError("要填文字")
            value = raw.strip()
            if item.kind == "choice":
                value = str(value).lower()
                if value not in item.choices:
                    raise ValueError(f"只能是 {' / '.join(item.choices)}")
            if item.field == "log_level":
                value = str(value).upper()
    except ValueError as exc:
        raise ValueError(f"{where} 設定錯誤:{exc}(收到 {raw!r})") from None
    return value


def load_settings(
    path: Path | None = None, env: dict[str, str] | None = None
) -> tuple[dict[str, object], list[str]]:
    """合併 程式預設 < settings.toml < 環境變數,回傳 (各欄位的值, 覆蓋警告)。"""
    env = dict(os.environ) if env is None else env
    path = path or Path(env.get("SETTINGS_FILE") or DEFAULT_SETTINGS_PATH)
    data: dict = {}
    if path.exists():
        with path.open("rb") as fh:
            data = tomllib.load(fh)

    known = {(item.section, item.key) for item in _ITEMS}
    for section, table in data.items():
        if not isinstance(table, dict):
            raise ValueError(f"{path.name} 的 {section} 必須是 [{section}] 區段")
        for key in table:
            if (section, key) not in known:
                raise ValueError(f"{path.name} 有不認得的設定 [{section}] {key},請檢查拼字")

    values: dict[str, object] = {}
    warnings: list[str] = []
    for item in _ITEMS:
        value = item.default
        in_file = item.key in data.get(item.section, {})
        if in_file:
            value = _convert(item, data[item.section][item.key], path.name)
        if env.get(item.env) is not None and env[item.env] != "":
            value = _convert(item, env[item.env], "環境變數")
            if in_file:
                warnings.append(
                    f"環境變數 {item.env} 覆蓋了 {path.name} 的 [{item.section}] {item.key}"
                )
        values[item.field] = value

    if not 0 < values["lull_min_seconds"] <= values["lull_max_seconds"]:
        raise ValueError("[lull] min_seconds 必須大於 0,而且不能大於 max_seconds")
    return values, warnings


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
    # 啟動時把自己在每個伺服器的暱稱改成角色名。
    sync_nickname: bool = True
    # 回覆別的 bot:off(只看不回)、mention(被 @ 或回覆才回)、all(跟成員一樣判斷)。
    bot_reply_mode: str = "all"
    # 防止 bot 互相接話停不下來:每頻道在這段時間內最多回 bot 幾次。
    bot_reply_max: int = 3
    bot_reply_window_seconds: float = 600.0
    # 用 Discord「回覆」引用對方訊息:auto(看情況)、always、off。
    reply_quote_mode: str = "auto"
    reply_quote_probability: float = 0.25  # auto 時,沒人插話也偶爾引用的機率
    reply_quote_ping: bool = False  # 引用時要不要通知對方
    # 允許角色 tag 最近在頻道講過話的人(永遠不會 @everyone、@here 或身分組)。
    tag_members: bool = True
    # 網頁後台:port 0 代表關閉;預設只聽本機。
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8787
    dashboard_token: str = ""
    # 載入時發現環境變數蓋掉 settings.toml 的項目,啟動後記進 log。
    overrides: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, settings_path: Path | None = None) -> "Config":
        """讀取 settings.toml 與環境變數建立設定,缺少必填項目時拋出例外。"""
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

        values, warnings = load_settings(settings_path)

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

        env = os.environ
        model = env.get("DEEPSEEK_MODEL", "deepseek-chat")
        return cls(
            discord_token=env["DISCORD_TOKEN"],
            deepseek_api_key=env["DEEPSEEK_API_KEY"],
            deepseek_base_url=env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            model=model,
            # 預留:之後可用較便宜的模型專做「該不該回」判斷。
            decision_model=env.get("DECISION_MODEL", model),
            disable_thinking=_parse_bool(env.get("LLM_DISABLE_THINKING", "false")),
            llama_server_exe=llama_exe,
            llama_server_args=llama_args,
            dashboard_token=env.get("DASHBOARD_TOKEN", ""),
            overrides=tuple(warnings),
            **values,
        )
