# 部署到 Debian（systemd 常駐）

讓漿糖在一台 Debian 主機上 24/7 跑著、開機自動啟動、crash 自動重啟。
以下假設使用者名為 `syrup`、專案放在 `/home/syrup/SyrupBot`，請自行替換。

## 0. 確認 Python 版本（重要）

本專案需要 **Python 3.11+**（用到標準庫 `tomllib`）。

```bash
python3 --version
```

- **Debian 12 (bookworm)**：內建就是 3.11，直接用。
- **Debian 11 (bullseye) 或更舊**：內建是 3.9，跑不起來。請改用 Docker，
  或自行安裝 3.11（backports / pyenv），不建議硬改系統預設 python。

## 1. 取得程式並安裝

```bash
sudo apt update && sudo apt install -y git python3-venv
git clone https://github.com/Syrup2718/SyrupBot.git
cd SyrupBot
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
# 用 -e . 安裝「專案本身」（會一併補齊相依）。這專案是 src/ 佈局，
# 只裝 requirements.txt 不會安裝 syrupbot 套件，會出現 "No module named syrupbot"。
.venv/bin/python -m pip install -e .
```

> `requirements.txt` 只是相依套件的清單，不含專案本身；部署一律用 `pip install -e .`。
> 用 `-e`（editable）之後，之後 `git pull` 更新程式碼，重啟 service 就生效，不必重裝。

## 2. 設定 .env（金鑰不會跟著 git 過來，要重新建）

```bash
cp .env.example .env
nano .env        # 填入 DISCORD_TOKEN；使用 DeepSeek 時也填 DEEPSEEK_API_KEY
```

若要在 Debian 上使用同機的 llama.cpp，可複製 `llm.local.env.example` 為 `llm.local.env`，
並將 `LLAMA_SERVER_EXE` 改成 Debian 主機上可執行的 `llama-server` 路徑。手動管理
`llama-server` 時可把 `LLAMA_SERVER_EXE` 留空，並填上對應的 API 位址與模型 ID。

從其他機器移轉時，先停止原本使用同一 bot token 的程序，避免兩邊同時處理訊息。

先手動跑一次確認沒問題（看到「已登入：漿糖…」就對了，然後 Ctrl+C 停掉）：

```bash
.venv/bin/python -m syrupbot
```

## 3. 裝成 systemd service

```bash
# 編輯範本，把 User 和兩個路徑改成你的實際值
nano deploy/syrupbot.service

# 複製到系統目錄
sudo cp deploy/syrupbot.service /etc/systemd/system/syrupbot.service

# 重新載入、設成開機自啟、立刻啟動
sudo systemctl daemon-reload
sudo systemctl enable syrupbot
sudo systemctl start syrupbot
```

## 4. 日常操作

```bash
sudo systemctl status syrupbot       # 看狀態（是否 active）
journalctl -u syrupbot -f            # 即時看 log（找「已登入」或錯誤）
sudo systemctl restart syrupbot      # 改了 settings.toml、.env 或人設後重啟套用
sudo systemctl stop syrupbot         # 停止
```

> 改了 `settings.toml`、`personas/*.toml`、`.env` 或 `llm.local.env` 後，要 `systemctl restart syrupbot` 才會生效。
> 行為參數（回覆機率、冷卻等）在 `settings.toml`，會跟著 `git pull` 更新；只想在主機上調整時，
> 可以在主機的 `.env` 加同名環境變數覆蓋（例如 `REPLY_PROBABILITY=0.3`），啟動 log 會提醒。
> 更新程式：`git pull` 後一樣 `restart`。

### 看網頁後台

後台只聽主機本機的 8787 埠，從自己的電腦開 SSH 通道：

```bash
ssh -L 8787:127.0.0.1:8787 使用者@主機
```

連上後在自己電腦的瀏覽器開 <http://127.0.0.1:8787/>。
