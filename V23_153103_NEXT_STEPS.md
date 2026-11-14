# 《流光城》11/14 15:31 版：下一階段進度與驗證

## 本次新交付：3 個檔案，全部新增、零覆蓋
- `tools/audit_v23_release_readiness.py`：只讀全系統接線檢查，輸出素材與關鍵缺口。
- `tests/verify_v23_release_audit.py`：檢查報告對現有程式的判定與只讀行為。
- `V23_153103_NEXT_STEPS.md`：本說明。

## 操作
在專案根目錄：

```bash
python tests/verify_v23_release_audit.py
python tools/audit_v23_release_readiness.py
python tools/audit_v23_release_readiness.py --json reports/v23_release_readiness.json
```

如需在 CI 上讓尚未完成的項目回傳錯誤碼，可加 `--strict`。程式不會連線 Discord 或寫入玩家資料庫。

## 重要狀態
- 24 個正式頂層指令與總覽導覽已對齊；NPC 類型改為選單。
- 580 個歷史素材編號中，目前 55 筆可正式入庫，525 筆缺舊版核准原文；已核准不需重新審核。
- 五筆歷史通知的程式與隔離測試通過，但尚未在 Discord 驗證送達。
- 玩家 NPC 初始金錢、正式行動值、角色論壇啟用後建立流程仍未完整串接；不要自行填入推測數額。
- 排行榜正式發獎開關仍關閉，不能不經核對就改成 True。

## 剩餘 6 個工作區塊（計數為工作項目，不等於固定檔案數）
1. 已核准歷史素材 525 筆原文恢復與安全入庫。
2. NPC 角色的已核准初始資源、金錢、行動值等福利串接。
3. 原創／NPC 角色正式啟用後四帖論壇建立與通知送達串接。
4. 排行榜正式派獎適配、試算及重入防護。
5. 24 指令跨系統交易、權限、資料一致性整合測試。
6. Discord／Railway 最後實機驗證。

這份進度清單不表示每個區塊只需一個檔案；未完成的工作未經逐檔設計不能準確預告最終「剩餘檔案數」。
