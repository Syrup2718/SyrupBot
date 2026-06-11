# SyrupBot

一個「住在 Discord 群組裡」的 LLM 聊天角色,由 **Python + discord.py + DeepSeek API** 打造。

她不是被 `/ask`、`/chat` 或 tag 才回答的工具型 bot,而是像群組裡的一個成員:
會看大家在聊什麼、**自己判斷什麼時候適合插話、什麼時候安靜**,並用簡短、自然、
可愛的語氣回覆——目標是讓人覺得親近,而不是在跟 AI 講話。

> 設計理念參考 [MaiBot](https://github.com/Mai-with-u/MaiBot) 的方向(更像真人,
> 而非更像完美助手),但**並非複製其程式碼或架構**,僅參考互動理念。

## 快速開始

```bash
# 1. 建立並啟用虛擬環境（建議，避免污染系統 Python；需要 Python 3.11+）
python -m venv .venv
source .venv/bin/activate          # Windows PowerShell: .\.venv\Scripts\Activate.ps1

# 2. 安裝相依
python -m pip install -e ".[dev]"

# 3. 設定環境變數
cp .env.example .env               # 填入 DISCORD_TOKEN 與 DEEPSEEK_API_KEY

# 4. 啟動
python -m syrupbot
```

啟動前的兩個準備:

1. 在 [Discord Developer Portal](https://discord.com/developers/applications)
   為 bot 開啟 **Message Content Intent**,否則它讀不到群組訊息內容。
2. 用 OAuth2 URL(`bot` scope,至少 `Send Messages` + `Read Message History`
   + `Add Reactions`(要讓她按表情)權限)把 bot **邀請進你的伺服器**。

## 她怎麼決定要不要說話

一則訊息進來後:

```
讀訊息 + 該頻道近期上下文
  → 判斷要不要回（預設用機率+加權規則，免費快速）
  → 要回的話，帶人設與上下文呼叫 DeepSeek
  → 把回覆「去 AI 化」：修語氣、砍客服腔、切短、加打字延遲
  → 送回 Discord
```

被 **@ 提及**或**被回覆**時她幾乎一定會回;一般訊息則看情況。判斷策略可用
環境變數 `DECISION_STRATEGY` 在 `heuristic`(規則)與 `llm`(讓模型讀氣氛判斷)
之間切換。

## 她也會按表情符號

除了講話,漿糖還會對訊息貼表情:**看到訊息時自己依機率挑表情貼**(關鍵字優先,
對不到就從通用表情池隨機挑),也會**跟著別人一起按**(別人按了、達門檻人數就跟)。
表情對應表在 [`personas/syrup.toml`](personas/syrup.toml) 的 `[reactions]`,行為機率
用下方旋鈕調。需要伺服器的 `Add Reactions` 權限。

## 可調旋鈕(`.env`)

改完重啟即生效(systemd:`sudo systemctl restart syrupbot`)。全部都有預設值,不填也能跑。

| 變數 | 預設 | 作用 |
|---|---|---|
| `DECISION_STRATEGY` | `heuristic` | 判斷要不要回的策略:`heuristic`(機率規則,免費)或 `llm`(讀氣氛判斷,每則花 token) |
| `REPLY_PROBABILITY` | `0.08` | heuristic 下,一般訊息的基礎插話機率(調高=更愛講話) |
| `REPLY_COOLDOWN_SECONDS` | `20` | 同頻道兩次主動回覆的最短間隔,防洗版 |
| `DECISION_COOLDOWN_SECONDS` | `10` | llm 策略下,每頻道觸發判斷的最短間隔,控 API 成本 |
| `MAX_CONTEXT_MESSAGES` | `20` | 餵給模型的近期訊息數 |
| `ALLOWED_CHANNEL_IDS` | (空) | 留空=所有頻道;填逗號分隔的頻道 ID=只在這些頻道互動 |
| `LLM_TEMPERATURE` | `1.1` | 回覆生成溫度,偏高讓語氣更口語 |
| `REACTIONS_ENABLED` | `true` | 表情功能總開關 |
| `SELF_REACTION_PROBABILITY` | `0.1` | 看到訊息時自己貼表情的機率 |
| `FOLLOW_REACTION_PROBABILITY` | `0.3` | 別人按了表情時跟著按的機率 |
| `FOLLOW_REACTION_MIN_COUNT` | `2` | 至少幾人按過她才跟(`1`=有人按就可能跟) |

> 完整變數說明見 [`.env.example`](.env.example)。

## 自訂人設

角色設定放在 [`personas/syrup.toml`](personas/syrup.toml),是純文字,直接編輯即可
調整名字、個性、說話習慣、語助詞、範例對話與表情對應表,**不需要改程式碼**。

## 開發

```bash
python -m pytest -q     # 執行測試
ruff check .            # Lint
```

詳細架構與開發指引見 [CLAUDE.md](CLAUDE.md)。
