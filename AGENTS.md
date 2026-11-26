# AGENTS.md

本文件適用於整個 repository，供在此專案工作的 coding agent 參考。

## 專案定位

SyrupBot 是一個以 Python、discord.py 和 OpenAI 相容 API 實作的 Discord 群聊角色，支援
DeepSeek 與 llama.cpp。
它不是等待 `/ask` 或 `/chat` 的工具型 bot，而是讀取頻道近期對話，自行判斷是否適合
插話，並用簡短、自然、符合人設的方式回應。它也能自行貼表情或跟隨其他成員的表情。

核心產品原則是「像群組成員，而不是完美助理」。修改回覆邏輯、人設或 prompt 時，應保留
自然、克制、不洗版、不使用客服腔的互動方式。

## 技術與常用指令

- Python 3.11+，採 `src/` layout。
- 執行期相依：`discord.py`、`openai`、`python-dotenv`。
- 開發工具：`pytest`、`pytest-asyncio`、`ruff`。
- 文字生成和 JSON 決策的 API 呼叫集中在 `llm.py`，本機服務程序由 `local_model.py` 管理。

Windows PowerShell 的典型開發流程：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
python -m syrupbot
```

驗證變更：

```powershell
python -m pytest -q
ruff check .
```

可針對單一範圍快速測試，例如：

```powershell
python -m pytest tests/test_decision.py -q
python -m pytest tests/test_decision.py::test_always_replies_when_mentioned -q
```

不要在測試或提交內容中使用真實 Discord token、DeepSeek key，或呼叫真實外部服務。
本機 `.env` 和 `llm.local.env` 可能含敏感資料。新增設定時更新 `.env.example` 或
`llm.local.env.example` 與相關文件。兩份範本只放占位值與可分享的預設值。

## 架構與資料流

主要訊息流程如下：

```text
Discord event
  -> bot.py：轉成 IncomingMessage
  -> reaction.py：逐則決定是否貼表情
  -> turns.py：按頻道收集同一人連發的訊息
  -> pipeline.py：記錄入站訊息並規劃回合
      -> decision.py：決定是否回覆
      -> llm.py：必要時判斷／生成文字
      -> postprocess.py：去除 AI／客服腔並切分訊息
  -> bot.py：按頻道依序送出，通知 pipeline 記錄已送達的文字
```

模組責任：

- `src/syrupbot/__main__.py`：組裝依賴並啟動 bot。production 中的
  `ChannelPolicy` 必須是 decision、reaction 與管理員指令共用的同一實例。
- `config.py`：唯一的環境變數讀取與驗證入口。其他模組接收 `Config`，不要自行讀取
  `os.environ`。
- `bot.py`：Discord I/O 接線層。保持精簡，不要把決策、機率或人設邏輯放進來。
- `turns.py`：短時間收集同一頻道同一成員的連發訊息，保留回合內的 @ 和回覆訊號。
- `pipeline.py`：回合的主編排層，規劃回覆與記錄實際送達的內容。
- `decision.py`：回覆決策核心。先套用硬規則，再走 `heuristic` 或 `llm` 策略。
- `reaction.py`：不碰網路的表情決策；Discord 訊息抓取與實際 `add_reaction` 留在
  `bot.py`。
- `gifs.py`：按人設清單挑選動圖，控制自動附圖的機率與冷卻。
- `context.py`：每頻道的短期記憶，僅存在記憶體，重啟會清空。
- `persona.py`：載入 `personas/*.toml` 並組 system prompt。人設內容應留在 TOML，
  不要硬編碼進流程。
- `llm.py`：LLM SDK 隔離層。`generate_reply` 用於已決定回覆的路徑，
  `judge_and_reply` 同時做 LLM 決策與生成。
- `local_model.py`：選用的 llama.cpp 啟動與健康檢查，bot 結束時只清理自身啟動的程序。
- `postprocess.py`：純函式的最終文字防線，並確保每段不超過 Discord 的 2000 字限制。
- `channels.py`：可持久化的頻道允許清單。
- `memory/`：尚未接入主流程的長期記憶介面；不要把它誤認為現有功能。
- `personas/syrup.toml`：名稱、暱稱、個性、語氣、興趣、範例與表情對應。
- `deploy/`：Debian/systemd 部署範本與說明。

## 必須維持的行為契約

- 所有非 bot 訊息都先記入近期上下文，即使最後不回覆；不同頻道的上下文必須隔離。
- bot 訊息、不允許的頻道、看似其他 bot 指令的內容會先被略過。
- 直接 `@` bot 或 reply bot 會略過一般回覆冷卻並回覆，但仍受更前面的 bot、頻道與
  指令過濾規則影響。
- Bot 實際送出回覆後，原發話者可在 60 秒內用明確的第二人稱接話，略過一般冷卻。
  其他成員插話、原發話者明確轉向別人，或接話資格已消耗時，回到一般決策。
- `heuristic` 使用可調機率與加權規則，不呼叫 LLM 做決策；`llm` 路徑用一次呼叫完成
  判斷與生成，並受 decision cooldown 限制。
- Bot 的發言與 reply cooldown 在 Discord 成功送出後更新。部分送出時只記錄已送達的段落。
- 表情決策與文字回覆決策彼此獨立，因此允許只貼表情、只回話、兩者都做或都不做。
- 頻道政策是 allowlist：空集合代表所有頻道開放；非空時只有列出的頻道可互動。
- `data/channels.json` 若存在，會覆蓋 `ALLOWED_CHANNEL_IDS` 的啟動初始值。不要反轉此優先序。
- 設定優先序為程序環境變數、`llm.local.env`、`.env`。設定檔與角色 TOML 改動需重啟才生效。
- LLM 判斷 JSON 無法解析時要保守地不回覆，不要猜測或直接放行。

## 修改原則

- 維持純邏輯與 I/O 分離。`decision`、`reaction`、`context`、`persona`、
  `postprocess` 應可在不啟動 Discord、不連網的情況下測試。
- 第三方 SDK 應包在專案自己的隔離層後面；不要讓核心流程直接依賴 SDK 物件。
- 涉及機率或時間的行為要保留可注入的 `rng`／`clock`，讓測試可重現。
- 新增 `Config` 欄位時，考慮為其提供預設值，以免既有測試中的 `Config(...)` 建構失效；
  同時補上解析、驗證、`.env.example` 與測試。
- 新增 persona 欄位時，盡量對缺省 TOML 值保持向後相容，並更新載入與 prompt 測試。
- 修改管理員斜線指令時，保留 `manage_guild` 權限限制與 ephemeral 回應。
- 不要為了新功能讓 `bot.py` 變成業務邏輯中心；先考慮新增純邏輯模組並由 pipeline 編排。
- 註解、docstring、人設與使用者可見文案使用繁體中文；識別字遵循現有英文命名。
- Ruff 行長上限為 88。遵循現有型別提示、frozen dataclass 與非同步介面風格。
- 不要順手提交執行期產物，例如 `.env`、`.venv/`、cache、log 或 `data/` 狀態檔。

## 測試對應

- 決策、冷卻、直接提及與策略切換：`tests/test_decision.py`
- 短期上下文與頻道隔離：`tests/test_context.py`
- 連發合併和回合邊界：`tests/test_turns.py`
- 回合整合、回覆檢查和送達後寫入：`tests/test_pipeline.py`
- 人設載入與關鍵字：`tests/test_persona.py`
- 去 AI 化與 Discord 長度切分：`tests/test_postprocess.py`
- 表情機率、關鍵字與頻道限制：`tests/test_reaction.py`
- 頻道政策持久化與空集合語意：`tests/test_channels.py`
- 模型啟動參數及程序所有權：`tests/test_local_model.py`

行為變更應在最接近的測試檔補回歸測試。涉及跨層編排時，可新增 pipeline 測試，但仍應
以 fake LLM 與可控制的亂數／時鐘保持離線、快速且穩定。

## 完成前檢查

1. 執行與修改範圍相符的測試；可行時執行完整 `python -m pytest -q`。
2. 執行 `ruff check .`。
3. 確認沒有讀取或提交 `.env`、token、API key、頻道狀態等敏感／執行期資料。
4. 若改了設定、指令、部署方式或使用者可見行為，同步更新 README、`.env.example` 或
   `deploy/README.md`。
5. 檢查 `git diff`，只保留本次任務需要的變更，不覆蓋使用者原有工作。
