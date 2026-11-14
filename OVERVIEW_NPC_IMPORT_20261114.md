# 《流光城》NPC 匯入選單及總覽一次性修正（2026-11-14）

基準：`discord-rp-bot-main(20261114-151256).zip`。

## 覆蓋（3 個）
- `modules/npc_history_v23.py`：群主按「匯入 NPC 預設人設」，先選「玩家 NPC／官方 NPC」，再開啟只需貼 Discord 訊息連結的表單；保留原雙審與素材管理功能。
- `modules/overview_catalog.py`：24 個正式頂層指令全收錄，移除不屬於正式頂層指令的「角色切換」。
- `modules/overview_data.py`：`/總覽` 標籤查詢不再 seed／寫資料庫；尚無標籤表時，讀 162 項核准 CSV 供唯讀預覽。

## 新增（2 個）
- `tests/verify_overview_npc_import_20261114.py`：離線靜態、SQLite 活動建表測試。
- `OVERVIEW_NPC_IMPORT_20261114.md`：本說明。

## 操作
1. 備份 GitHub 專案及部署環境的資料庫。
2. 將本次更新 ZIP 解壓，三個原有 `.py` 檔依路徑覆蓋，另兩個新增。
3. 在專案根目錄執行：

```bash
python tests/verify_overview_npc_import_20261114.py
python -m compileall -q core modules tools tests
```

4. 全部檔案整合後，才在測試 Discord 伺服器確認 `/官方` NPC 入口、選單、訊息連結、人設解析，及 `/總覽` 的各分類。

## 不需要修改的檔案
- `modules/event_project_manager.py`：重新檢查 15:12 版本的 `rp_event_project_audit`，只存在一個 `detail` 欄位；隔離執行全部建表 SQL 已通過，前次重複欄位報告不適用這個版本。
- `modules/z_system_hub.py`：已從 `overview_catalog.SYSTEMS` 動態產生導覽，更新分類後不必覆蓋。
- `main.py`、歷史素材庫、NPC 初始數值與其他所有模組不變。

## 範圍與限制
這次修復已確認的 NPC 匯入 UI 與總覽問題，不宣稱 24 個系統功能已全部通過 Discord/Railway 實機測試。未改動正式 DB、角色、金錢、NPC 配點或新人禮包。
