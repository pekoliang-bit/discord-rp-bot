# V2.3 歷史素材：沿用先前已核准內容

- 使用者已明確確認 **全部 580 項早先均已核准**，不需要重新審核。
- 目前能忠實恢復並直接匯入 **55 項**：HB-0001～0050 共 50 項，HE-0151、HE-0166、HE-0186、HE-0206、HE-0221 共 5 項。
- **525 項仍缺原始文字**：WN 200、CN 100、HE 正負面150、HE 代言其餘75。這是「已核准但原文尚未找回」，不是「待審核」。
- 不能用新編內容或空白代替既有原文；素材檔中的待恢復項 `import_ready=false`，防止錯誤抽取。
- `assets/history_v23/HB_0001_0045_approved.json` 直接記錄早先核准的45個品牌，保留原名稱、級別與類別。
- `assets/history_v23/verified_import_ready.json` 是可直接匯入的55筆，不需重新核准。

## 使用方法

先備份正式資料庫，並以測試環境執行：

```bash
python tests/verify_history_v23_assets.py
python tests/verify_history_v23_review_safety.py
python tools/import_history_v23_assets.py --guild 1544051203493601380
```

確認測試資料庫路徑 `DATABASE_PATH` 後再執行真正匯入：

```bash
python tools/import_history_v23_assets.py --guild 1544051203493601380 --apply
```

`--apply` 不會發給角色金錢或履歷，也不會修改既有玩家資料；只更新歷史素材資料庫。同編號不同內容會拒絕覆蓋，保護既有正式素材。

## 下一步

恢復525筆已核准文字；不再逐筆提請使用者核准。
