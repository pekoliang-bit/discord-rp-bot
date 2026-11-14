# 流光城｜歷史履歷 V2.3：五筆私人通知一次性整合

底版：`discord-rp-bot-main(20261114-143848).zip`。

## 本次檔案（每個檔案只有一份最終版）

新增：
- `core/history_v23_delivery.py`：讀取已鎖定五筆快照，格式化並排程角色論壇的私人歷史通知。
- `tests/verify_history_v23_delivery.py`：隔離資料庫測試完整五筆、訊息長度、提及安全與防重送。
- `HISTORY_V23_NOTICE_INTEGRATION_20261114.md`：本說明。

覆蓋：
- `core/character_activation_v23.py`：原創角色和玩家 NPC 啟用後的歷史通知連接。
- `modules/npc_history_v23.py`：舊角色歷史補建後的私人通知連接。

`main.py`、素材庫、數值、銀行與新人禮包模組均不變。

## 更新方式

1. 備份正式程式碼、Git commit 及 SQLite 資料庫。
2. 在測試專案使用本次「完整 ZIP」，或把「更新 ZIP」的五個檔案按相對路徑放置，不要再套其他舊版同名檔。
3. 專案根目錄執行：
   ```bash
   python -m compileall -q core modules tools tests
   python tests/verify_history_v23_delivery.py
   python tests/verify_history_v23_materials_one_pass.py
   python tests/verify_npc_birthday_activation_v23.py
   ```
4. 測試 Discord 伺服器必須先設定角色專屬論壇，並確認角色有 `notice_id`。成功抽足五筆後，通知會加入既有 `notification_outbox`，由通知中心送至論壇的固定「通知」帖子。這不是 Discord DM。
5. 如已啟用舊角色之五筆履歷後來才補建，使用 `/官方` 既有的「補建待完成歷史」功能，補建成功才會排程私人通知。

## 不變的規則

- 正式審核通過及角色 `active` 後才抽歷史；未抽足五筆絕不發五筆結果通知。
- 同一角色只排程一次，`dedupe_key=v23:history:five:<guild>:<character>`。
- 歷史通知不發現金、屬性、點數、商品或現役代言。
- 新人禮包仍在原有入口，由玩家自主領取，每角色限一次，這次不修改獎池與領取機制。
- 原始素材：55 筆有正式可匯入內容，517 筆新編候選不可冒充已核准原文，8 筆 WN 缺原文。

## 尚待完成

- 找回其餘 525 筆既有核准原始素材，否則五筆抽選通常會停在 `pending_assets`。
- 核對原創角色和玩家 NPC 的原始金錢、行動值、點數等啟用福利來源，合併另一個對話維護的正式 NPC 數值方案，**不得自行發明金額或公式**。
- 角色論壇四個固定帖子、Discord UI、Railway 與真實通知送達仍需實機驗證。
