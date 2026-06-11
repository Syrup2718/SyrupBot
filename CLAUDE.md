# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 專案概觀

SyrupBot 是一個「**住在 Discord 群組裡的 LLM 角色**」,不是指令型工具 bot。
設計理念參考 MaiBot 的方向:**更像真人互動,而不是更像完美助手**——它會讀群組
對話、自己判斷該不該插話、用簡短自然的語氣回覆,而不是被 `/ask` 或 tag 才回答。

技術棧:**Python + discord.py + DeepSeek API**(DeepSeek 與 OpenAI SDK 相容,
透過 `openai` 套件、把 base_url 指向 DeepSeek)。

人設目標:可愛但不浮誇的少女感,偶爾用「餒/窩/對不擠/啦/齁」等語助詞但不浮濫。

## 常用指令

```bash
# 安裝（含開發相依）
python -m pip install -e ".[dev]"

# 執行（需先設定好 .env）
python -m syrupbot

# 全部測試 / 單一檔 / 單一測試
python -m pytest -q
python -m pytest tests/test_decision.py
python -m pytest tests/test_decision.py::test_always_replies_when_mentioned

# Lint
ruff check .
```

> 註:此開發機處於自簽憑證/代理環境,`pip install` 可能 SSL 憑證驗證失敗。
> 若遇到,加上:`--trusted-host pypi.org --trusted-host files.pythonhosted.org`。

執行前需 `cp .env.example .env` 並填入 `DISCORD_TOKEN`、`DEEPSEEK_API_KEY`,
且在 Discord Developer Portal 開啟 **Message Content Intent**(否則讀不到訊息內容)。
設定缺漏時 `Config.from_env()` 會在啟動時直接拋錯。

## 架構

採 `src/` layout。核心是一條**訊息處理管線**:一則訊息進來後,依序經過
「上下文 → 決策 → 生成 → 後處理」,每一層都是獨立、可替換、可單獨測試的模組。
未來的進階功能都是「**往管線插一層**」或「**替換某層實作**」,而非重寫架構。

```
discord 事件
  └─ bot.py            把 discord.Message 抽成 IncomingMessage(框架無關)
       └─ pipeline.py  主編排:記上下文 → 問決策 → 生成 → 後處理 → 回傳
            ├─ context.py      每頻道近期逐字稿(記憶體,含發話者名)
            ├─ decision.py ★   該不該回:硬規則前置過濾 + 策略(heuristic/llm)
            ├─ persona.py      讀 personas/*.toml,組 system prompt
            ├─ llm.py          DeepSeek 隔離層(generate_reply / judge_and_reply)
            └─ postprocess.py  去 AI 化:砍客服腔、去條列、切短
```

各模組職責:

- **`config.py`** — 唯一設定來源。所有環境變數啟動時讀進 frozen `Config` 並驗證。
  其他模組一律接收 `Config`,不自己讀 `os.environ`。

- **`bot.py`** — Discord 接線層,**刻意做薄**。只負責抽訊息、呼叫 pipeline、
  加打字延遲送回。不放任何判斷邏輯。

- **`pipeline.py`** — 主編排(`ChatPipeline.handle`)。所有「訊息進來到回覆出去」
  的流程都在這。新功能優先考慮在這裡插一層。

- **`decision.py` ★核心** — 「是否回覆」決策,SyrupBot 與傳統 bot 的本質差異。
  - **第一關(免費硬規則)**:略過 bot/自己、不在白名單的頻道、別人的指令前綴、
    冷卻時間內的訊息。
  - **捷徑**:被 @ 提及或被 reply → 直接回(不受一般冷卻限制)。
  - **第二關(策略,由 `DECISION_STRATEGY` 切換)**:
    - `heuristic`:機率+加權規則(問號、提到名字、興趣關鍵字、最近是否在對話、
      訊息長短),**不花 API token**。
    - `llm`:呼叫 `judge_and_reply`,讓模型讀上下文自己決定,**一次呼叫同時產生
      回覆**;以 `DECISION_COOLDOWN_SECONDS` 限制每頻道判斷頻率來控成本。
  - 冷卻狀態存在引擎內;pipeline 送出回覆後呼叫 `record_reply()` 更新。

- **`persona.py`** — 從 `personas/<PERSONA>.toml` 載入角色,組出 system prompt
  (身分+個性+說話守則+語助詞「適量」指示+few-shot 範例+禁則)。few-shot 是讓
  它「像那個角色」最有效的手段。

- **`llm.py`** — LLM 供應商隔離層。唯一知道 `openai`/DeepSeek 細節的地方。
  `generate_reply`(heuristic 路徑:已決定要回,只生成);`judge_and_reply`
  (llm 路徑:回傳 JSON 的判斷+回覆,解析失敗時保守地當作不回)。

- **`context.py`** — 每頻道近期上下文(`ConversationStore`),記憶體、重啟即清空,
  含發話者名;`transcript()` 組成給 LLM 的逐字稿。這是**短期上下文**,不是長期記憶。

- **`postprocess.py`** — 把 LLM 輸出去 AI 化的純函式:砍客服腔開頭/結尾、去條列與
  標題、剝外層引號、切成 ≤2000 字的段。Prompt 會漏,這裡是程式層的最後把關。

- **`memory/`** — **長期記憶的擴充點,第一階段尚未接入主流程**。定義了
  `MemoryStore` 介面與 `NullMemory`(no-op)。之後接 JSON/SQLite/向量庫時,
  上層依賴介面不必改。

- **`personas/*.toml`** — 角色設定,與程式碼分離。改人設只編輯 TOML;多角色就多開檔、
  把 `PERSONA` 指過去。

## 行為調校的旋鈕(環境變數)

- `DECISION_STRATEGY`:`heuristic`(預設,省成本)或 `llm`(更聰明、每則都花)。
  **兩種都已實作,翻開關即切換**——這是「之後升級成 LLM 判斷氣氛」的預留路徑。
- `REPLY_PROBABILITY`:heuristic 的基礎插話機率。想讓它更愛講話就調高。
- `REPLY_COOLDOWN_SECONDS` / `DECISION_COOLDOWN_SECONDS`:防洗版 / 控 API 成本。
- `ALLOWED_CHANNEL_IDS`:留空=所有頻道;填 ID=只在指定頻道互動。
- `LLM_TEMPERATURE`:回覆生成溫度,偏高(預設 1.1)讓語氣更口語。

## 慣例與擴充原則

- **純邏輯與 I/O 分離**:`decision`(硬規則部分)、`context`、`postprocess`、
  `persona` 都不碰 Discord/網路,可純函式測試。測試用假 LLM + 可注入的
  `rng`/`clock`(見 `tests/test_decision.py`)讓「該不該回」完全可預測。
- **隔離層模式**:任何第三方 SDK(LLM、未來的 DB)都包在自家類別後,核心依賴
  自家介面而非 SDK,方便替換。
- **「去 AI 化」是雙保險**:prompt 層(system prompt 禁客服腔/要求簡短)+ 取樣層
  (高溫)+ 後處理層(程式強制)。三者一起,不靠單一手段。
- 程式碼註解、docstring、人設文案使用**繁體中文**。
- MVP 範圍:只做文字聊天。語音、圖片理解、Web UI、插件、資料庫都是之後的事。
