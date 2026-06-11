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
.venv/bin/python -m pip install -r requirements.txt
```

## 2. 設定 .env（金鑰不會跟著 git 過來，要重新建）

```bash
cp .env.example .env
nano .env        # 填入 DISCORD_TOKEN 與 DEEPSEEK_API_KEY
```

> ⚠️ 同一個 bot token 一次只能有一個程式在連。啟動這台之前，
> 先把你本機（或其他地方）還在跑的漿糖關掉，否則兩邊會互踢、連不上。

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
sudo systemctl restart syrupbot      # 改了 .env 或人設後重啟套用
sudo systemctl stop syrupbot         # 停止
```

> 改了 `personas/*.toml` 或 `.env` 後，要 `systemctl restart syrupbot` 才會生效。
> 更新程式：`git pull` 後一樣 `restart`。
