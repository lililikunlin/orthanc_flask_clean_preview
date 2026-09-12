# 遠距醫療影像存取安全系統 (TMISAS)
**Telemedicine Medical Image Secure Access System**

基於 Flask 安全網關、Orthanc PACS 伺服器與物聯網 (IoT) 邊緣運算技術，為遠距醫療環境打造的「Edge-to-Cloud」醫療影像安全存取與權限控管解決方案。

## ✨ 核心特色與亮點 (Core Features)

* **🔒 Edge-to-Cloud 輕量化安全傳輸 (HMAC-SHA256)**
  專為運算資源受限的物聯網設備（如 Raspberry Pi）設計。邊緣設備在擷取影像後，使用 HMAC 動態簽章與 Unix 時間戳記進行封裝。後端伺服器具備防重放攻擊 (Replay Attack) 與時間差攻擊防護機制，確保機器對機器 (M2M) 傳輸的不可偽造性。
* **👥 嚴謹的角色存取控制 (RBAC)**
  系統內建 `Patient` (病患)、`Doctor` (醫師)、`Admin` (管理員) 三種角色。透過後端 `@permission_required` 裝飾器，落實最小權限原則，精準攔截未經授權的 API 請求（上傳、修改、刪除、調閱）。
* **🏥 Orthanc PACS 系統無縫整合與容錯預覽**
  將上傳之 JPEG 影像自動封裝為 DICOM 標準格式並歸檔至 Orthanc。針對臨床 DICOM 檔案編碼多樣性的問題，實作了「多端點預覽容錯機制」，結合 Pillow 套件動態轉檔 PNG，確保網頁端影像載入率近乎 100%。
* **🔑 FIDO2 無密碼身分鑑別 (擴充模組)**
  初步導入 WebAuthn 標準，支援跨裝置 (Cross-device) 生物辨識與硬體金鑰登入，降低傳統密碼外洩風險，邁向零信任 (Zero Trust) 安全架構。

## 🏗️ 系統架構 (System Architecture)

本專案軟體層次模組化為三大子系統：
1. **FUDDS (前端介面與動態展示子系統):** 處理醫療儀表板、DICOM 預覽與使用者互動。
2. **BSGACS (後端安全網關與權限控管子系統):** 負責 Session 管理、HMAC 簽章驗證與 RBAC 權限攔截。
3. **OIIPS (Orthanc 串接與影像處理子系統):** 負責與底層 Orthanc 伺服器進行 DICOM 查詢、上傳與轉檔處理。