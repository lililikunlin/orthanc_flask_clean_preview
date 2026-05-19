# Orthanc × Flask 後端串接系統

## 帳號

- patient1 / 1234：可檢視、修改、移除
- doctor1 / 1234：可檢視、加入
- admin1 / 1234：只可檢視

## 執行步驟

1. 確認 Orthanc 已經開啟，網址通常是：

```text
http://127.0.0.1:8042
```

2. 進入專案資料夾：

```bat
cd C:\Users\Cangshu\Downloads\orthanc_flask_clean_preview
```

3. 安裝套件：

```bat
py -m pip install -r requirements.txt
```

4. 建立環境設定檔：

```bat
copy .env.example .env
```

5. 啟動 Flask：

```bat
py app.py
```

6. 開啟網頁：

```text
http://127.0.0.1:5000
```

## 預覽功能說明

這版預覽功能已加強，會依序嘗試：

1. `/instances/{id}/preview`
2. `/instances/{id}/frames/0/preview`
3. `/instances/{id}/image-uint8`
4. `/instances/{id}/rendered`
5. `/instances/{id}/frames/0/rendered`

如果 Orthanc 回傳的圖片格式瀏覽器不能直接顯示，Flask 會用 Pillow 轉成 PNG 後再送到前端。

如果仍然無法預覽，可能是該 DICOM 沒有影像像素資料、影像壓縮格式 Orthanc 無法解碼，或該 instance 本身不是可顯示影像。
