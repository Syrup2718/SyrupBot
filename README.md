# SyrupBot

漿糖是一個住在 Discord 群組裡的聊天角色。她會讀取近期對話，自行決定何時插話或貼表情，
通常用一兩句話回覆。專案使用 Python 3.11+ 和 discord.py，支援 DeepSeek 與本機 llama.cpp。

群聊互動設計參考 [MaiBot](https://github.com/Mai-with-u/MaiBot)。

## 快速開始

從專案根目錄執行。Windows PowerShell 安裝指令如下。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Linux 使用 `.venv/bin/python`，首次設定時用 `cp -n .env.example .env`。常駐服務請參考
[Debian／systemd 部署說明](deploy/README.md)。

1. 在 Discord Developer Portal 開啟 **Message Content Intent**，並把 bot token 填進 `.env`
   的 `DISCORD_TOKEN`。 [Discord Developer Portal](https://discord.com/developers/applications)
2. 邀請 bot 時選擇 `bot` 和 `applications.commands` scope，並給它預計互動頻道的
   `View Channel`、`Send Messages`、`Read Message History` 和 `Add Reactions` 權限。
3. 選擇模型來源。使用 **DeepSeek** 時，在 `.env` 填入 `DEEPSEEK_API_KEY`。
   使用 **llama.cpp** 時，依下方說明建立 `llm.local.env`。
4. 啟動後看到「已登入」，就可以在允許互動的頻道 `@漿糖` 測試。

```powershell
.\.venv\Scripts\python.exe -m syrupbot
```

## 使用本機 llama.cpp

先準備與電腦相容的 `llama-server`。範本使用 Windows CUDA 執行檔，在 `9932` 埠啟動
Qwen3.5 9B，模型 alias 為 `qwen-chat`，context 為 `16384`。首次設定時執行：

```powershell
if (-not (Test-Path llm.local.env)) {
    Copy-Item llm.local.env.example llm.local.env
}
```

| 設定項目 | 如何調整 |
| --- | --- |
| `LLAMA_SERVER_EXE` | 填入本機執行檔路徑。手動啟動服務時留空。 |
| `LLAMA_SERVER_ARGS` | JSON 字串陣列。模型、GPU 和 context 等參數可以在這裡調整。 |
| `DEEPSEEK_BASE_URL` | 自動啟動使用 `http://127.0.0.1:<port>/v1`，host 和 port 會帶入啟動參數。 |
| `DEEPSEEK_MODEL` | 自動啟動時用作 alias。手動啟動時填 `/v1/models` 回傳的模型 ID。 |
| `DECISION_MODEL` | `llm` 策略使用的模型。更換生成模型時，可一併改成同一個 ID。 |
| `DEEPSEEK_API_KEY` | 服務未啟用 API 驗證時可用範本的 `local-no-key`。 |
| `LLM_DISABLE_THINKING` | 範本為 `true`，適用於支援 `enable_thinking` 的模型。 |

想試用短思考時，把 `LLM_DISABLE_THINKING` 設為 `false`，在 `LLAMA_SERVER_ARGS` 陣列
加上 `"--reasoning", "on", "--reasoning-budget", "64"`，再重啟 bot 和 llama.cpp。
`64` 是思考的 token 預算，回覆本身與實際耗時會另外計算。

自動啟動時，bot 會等模型就緒才登入 Discord。已有同一模型在監聽時會共用該服務，
關閉 bot 時只會停止它自己啟動的模型程序。若啟動失敗，查看同一終端機的 `llama-server` 輸出。

`DEEPSEEK_*` 同時用於設定 OpenAI 相容的本機 API。設定優先序為
**程序環境變數 → `llm.local.env` → `.env`**。重新啟動 bot 後套用。移開 `llm.local.env`
並重新啟動，即可回到 `.env` 的設定。 [本機設定範本](llm.local.env.example)

## 她怎麼決定要不要說話

一則訊息進來後:

```
讀訊息 + 該頻道近期上下文
  → 判斷要不要回（預設用機率+加權規則，免費快速）
  → 要回的話，將人設與上下文交給選定的模型
  → 把回覆「去 AI 化」：修語氣、砍客服腔、切短、加打字延遲
  → 送回 Discord
```

通過頻道與指令過濾後，**@ 漿糖**或**回覆她的訊息**會直接觸發回覆。一般訊息則由
`settings.toml` 的 `[reply] strategy` 決定。`heuristic` 使用本機規則與機率，`llm` 會請模型依上下文判斷，
`hybrid` 則先用本機規則決定要不要看、抽中才問模型。

她講得越多，主動插話的機率就越低（話量疲勞），之後隨時間慢慢恢復；被 @ 或回覆不受影響。

大家聊了一陣子後突然安靜很久時，她偶爾會自己接一句（冷場接話），沒有想說的就不說。

別的 bot 說的話她也會回，判斷方式跟成員一樣（`[bots] reply_mode = "all"`）。為了避免兩個 bot
互相接話停不下來，每個頻道有回 bot 的次數上限（`reply_max` / `reply_window_seconds`）；
只想在被 bot @ 時回可設 `mention`，完全不回設 `off`。她不會對 bot 的訊息貼表情。

她回話時會像真人一樣看情況用 Discord 的「回覆」引用對方訊息：中間有別人插話、或對方本來就是
在回覆她時一定引用，其他時候偶爾引用（`[quote]`）。冷場接話時引用的是她接的那則。
預設不會因引用而通知對方（`[quote] ping`）。

需要點名時她會 tag 人，例如同時跟好幾個人聊、或想叫某人來看。只能 tag 最近在這個頻道講過話的人，
永遠不會 @everyone、@here 或身分組；不想讓她 tag 人可設 `[tag] enabled = false`。

同一成員在同一頻道連發時，漿糖預設等約 0.8 秒，對方每多打一則就再多等一下，最多把四則合成一個回合判斷一次。頻道很熱絡時等待減半，被 @ 時也不會等太久。
換人發言時會結束前一個回合。每個頻道按順序判斷、生成和送出，回覆確認送出才會進入短期記憶。

漿糖剛回覆你時，60 秒內接一句「你呢？」或「所以你剛剛說什麼？」也會直接回覆。
其他成員插話，或你明確轉向其他成員時，這段連續對話就會中斷。

較長的回覆會在句尾或換行處拆成幾則，中間停一下再接著送出。

## 她也會按表情符號

漿糖會依機率主動貼表情，優先選用與關鍵字對應的表情，其他時候從通用表情池挑選。
其他成員按的表情達到門檻後，她也有機會跟著按。表情對應表在角色檔的 `[reactions]`。
[角色設定](personas/syrup.toml)

## 發 GIF

在允許互動的頻道說 `@漿糖 發月薪貓` 就會從系列中挑一張。也可以指定動作，例如
`@漿糖 發個月薪貓跳舞的貼圖`。平時聊天命中個別貼圖的關鍵字時，漿糖會另外決定是否發 GIF。
Discord 要在頻道內直接預覽動圖，需要 `Embed Links` 權限。

動圖和關鍵字可在角色設定檔的 `[gifs]` 修改。每個 `stickers` 項目對應一張動圖，包含
`label`、`keywords` 和公開 HTTPS `.gif` 直連 `url`。命中多張時優先使用較長的關鍵字。
在 `settings.toml` 的 `[gifs]` 可關閉功能，或調整自動發圖機率與同一頻道的最短間隔。

## 查看模型狀態

伺服器成員可輸入 `/llmstatus`，查看模型名稱、本頻道保留的上下文訊息數、bot 進行中的
模型請求數，以及本頻道最近一次完成請求的時間與 token 用量。Embed 卡片只對執行者顯示。

llama.cpp 回傳速度時，會顯示輸入處理與生成的 tok/s。其他 API 若只回傳 token 數，則以
整次請求的耗時計算平均輸出速度。輸入 token 包含人設與該次請求的對話上下文。
查詢使用 bot 在記憶體中記錄的數字，不會另外呼叫模型。

## 網頁後台

bot 啟動後，用瀏覽器打開 <http://127.0.0.1:8787/> 就能看到：

- 每個頻道目前的上下文（她「看得到」的對話）、話量疲勞和冷卻剩餘時間。
- 最近事件：成員發言、她的回覆判斷與理由、實際送出的回覆、冷場接話。
- **別的 bot 的訊息有沒有被記下**：綠色「bot 已記」代表有進上下文；紅色「bot 略過」會寫出原因
  （例如沒有文字內容、頻道不在白名單）。

頁面每 3 秒自動更新，資料只存在記憶體，重啟就清空。預設只聽本機；bot 跑在主機上時，
用 SSH 通道從自己電腦連：`ssh -L 8787:127.0.0.1:8787 使用者@主機`，再開上面的網址。
位址在 `settings.toml` 的 `[dashboard]`；要對外開放時請在 `.env` 設 `DASHBOARD_TOKEN`。

## 管理員指令

`/llmclear` 可清空漿糖在目前頻道的短期記憶。需要「管理訊息」權限，結果只對執行者顯示。
此指令只清除 bot 的記憶，Discord 上的訊息與其他頻道不受影響。

有「管理伺服器」權限的人可以用 `/set` 即時調整允許互動的頻道。指令回應只對執行者
顯示，設定會保存在 `data/channels.json`。

| 指令 | 作用 |
|---|---|
| `/set channel channel:#x` | 只在 #x 回話和貼表情 |
| `/set allow channel:#x` | 把 #x 加入允許清單 |
| `/set remove channel:#x` | 把 #x 移出允許清單 |
| `/set all` | 解除頻道限制 |
| `/set status` | 查看目前允許互動的頻道 |

> 語意是**允許清單**:清單為空 = 所有頻道都可以(預設)。一旦清單有內容,就只在清單內
> 的頻道講話與貼表情。移除最後一個頻道也會回到全部開放。
> `settings.toml` 的 `[channels] allowed_ids` 是初始值，有存檔後以 `data/channels.json` 為準。
>
> ⚠️ 斜線指令需要邀請時帶 **`applications.commands`** scope。若指令沒出現,多半是
> 當初邀請只給了 `bot` scope —— 用含兩個 scope 的網址**重新邀請一次**即可(不會踢掉
> bot、不會掉資料)。

## 參數設定

所有可調的行為參數都在專案根目錄的 [`settings.toml`](settings.toml)，依功能分組、每項都有中文說明，
改完重啟 bot 生效。`.env` 只放密鑰與模型連線（Discord token、API key、模型名稱）。

| 區段 | 內容 |
|---|---|
| `[persona]` | 用哪張角色卡、要不要同步伺服器暱稱 |
| `[reply]` | 判斷策略、插話機率、冷卻、話量疲勞、連發合併、上下文長度、生成溫度 |
| `[lull]` | 冷場接話的機率與等待時間 |
| `[bots]` | 回不回別的 bot、連鎖上限 |
| `[quote]` | 用「回覆」引用對方訊息的方式 |
| `[tag]` | 能不能 tag 人 |
| `[reactions]` / `[gifs]` | 表情與動圖 |
| `[channels]` | 允許互動的頻道（初始值） |
| `[dashboard]` / `[log]` | 網頁後台位址、log 等級 |

設定值打錯（例如機率填 1.5、拼錯鍵名）時，bot 啟動會直接報錯並指出是哪一項。

`.env` 也能用環境變數覆蓋 `settings.toml` 的任何一項，例如 `REPLY_PROBABILITY=0.5`，
方便在主機上臨時調整；有覆蓋時啟動 log 會出現警告。變數名稱對照見
[`src/syrupbot/config.py`](src/syrupbot/config.py) 的 `_ITEMS`。

## 自訂人設

漿糖的預設人設是 16 歲的少女網友，有雪白中長髮、琥珀橘雙眼和橘色四角星髮夾，喜歡
遊戲、音樂和迷因。角色 TOML 可調整名字、個性、說話習慣、範例對話
與表情，修改後重啟 bot。 [角色設定](personas/syrup.toml)

## 開發

```bash
python -m pip install -e ".[dev]"
python -m pytest -q     # 執行測試
ruff check .            # Lint
```

測試使用假的 LLM 和測試資料，可在未啟動 Discord 或 llama.cpp 的情況下執行。
架構和修改慣例見 [AGENTS.md](AGENTS.md)。
