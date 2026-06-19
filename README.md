# Orthanc × Flask 醫療後端系統 - GCP 雲端維護終極指南

這份文件完整記錄了在 Google Cloud Platform (GCP) 上維護、更新與除錯此專案的所有必備指令。
專案網域：`https://telemed-sec.duckdns.org:5000`

---

## 🚀 一、伺服器啟動與背景執行 (Tmux & Gunicorn)

本專案使用 `tmux` 讓程式在背景 24 小時運行，並使用 `gunicorn` 確保多線程穩定性。

### 1. 日常巡邏 (查看與進出背景)
* **查看目前背景有哪些螢幕：** `tmux ls`
* **穿越進入名為 med 的螢幕：** `tmux attach -t med`
* **金蟬脫殼 (退出並保持背景執行)：** 按下 `Ctrl + B`，雙手放開，再按 `D`。

### 2. 啟動與重啟系統
* **進入專案資料夾：** `cd ~/orthanc_flask_clean_preview`
* **啟動 Python 虛擬環境：** `source venv/bin/activate`
* **啟動 Gunicorn 武裝伺服器 (附帶 HTTPS 綠色鎖頭)：**
  ```bash
  sudo ./venv/bin/gunicorn --certfile=/etc/letsencrypt/live/telemed-sec.duckdns.org/fullchain.pem --keyfile=/etc/letsencrypt/live/telemed-sec.duckdns.org/privkey.pem --bind 0.0.0.0:5000 app:app
