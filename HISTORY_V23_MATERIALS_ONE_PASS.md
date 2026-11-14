
# 《流光城》歷史素材庫 V2.3｜一次性檔案整合版

基礎版本：`discord-rp-bot-main(20261114-132855).zip`。
**本次只改素材庫：不動 NPC 初始數值、初始金錢、現役代言、已存在角色履歷、新人禮包及正式玩家資料。**

## 重要：以前核准的 580 項不必重審

- 580 個正式編號及既有核准規格均予保留。
- **55 項已找回可正式匯入的名稱或原文**：歷史品牌 HB 50、對應五個錨定歷史代言 HE 5。
- **517 項有後來新編重建候選內容，不是舊時逐筆核准的原文**：WN 192、CN 100、正面 HE 75、負面 HE 75、代言 HE 75。這些稿件保留供找回原文時對照，但不允許直接匯入核准抽選池。
- **8 項連可解析完整候選稿都沒有**：WN-0193～WN-0200。
- **525 項仍欠缺舊版核准原文**；它們是「已核准，但原文待恢復」，**不是待重新審核**。
- 原 ZIP 中 `WN_0001_0200_rebuilt_import.json` 被截斷。本次只將可完整解析的 192 筆整理成合法 JSON，不補寫第 193～200 筆，也不變造 192 筆內容。
- 新增 `HE_0151_0230_approved_rarity_mapping.json`，單獨保存已核准的 80 筆 R1～R5／B1～B5 對照；此對照**不代表 75 筆新編代言文案已是原稿**。

## 檔案如何使用

推薦使用「完整機器人 ZIP」。若已在 GitHub 保留 13:28 版，可使用「僅新增／修改檔 ZIP」覆蓋，但**不要額外覆蓋先前同名版本**；以本次單一整合包為準。

### 1. 先備份資料庫

```bash
python tools/backup_history_v23_database.py --output backups/pre_history_v23_import.sqlite3
```

執行前確認 `DATABASE_PATH` 是你想要備份的資料庫。如果備份檔已存在，工具會拒絕覆蓋它。

### 2. 靜態與隔離資料庫測試（不碰正式資料）

```bash
python -m compileall -q core modules tools tests
python tests/verify_history_v23.py
python tests/verify_history_v23_assets.py
python tests/verify_history_v23_review_safety.py
python tests/verify_history_v23_materials_one_pass.py
```

### 3. 查看素材統計（不改資料庫）

```bash
python tools/import_history_v23_assets.py --audit
```

如需重新生成 580 個**編號與狀態索引**（不改正式 DB，不改候選文字）：

```bash
python tools/build_history_v23_catalog.py
```

### 4. 預覽可匯入資料（預設 dry run）

```bash
python tools/import_history_v23_assets.py --guild 1544051203493601380
```

### 5. 實際匯入 55 筆（只在確認資料庫及備份後）

```bash
python tools/import_history_v23_assets.py --guild 1544051203493601380 --apply
```

`--apply` 只寫入 `rp_history_assets_v23`，不建立角色、不抽歷史、不發任何獎勵；同樣內容再匯入會略過，異內容同編號則整批回滾。**這不是自動替正式 Railway 執行資料庫匯入**。

未來取得其他已核准原稿，可將已恢復的素材整理為合法 JSON，使用多個 `--file` 合併進一個交易：

```bash
python tools/import_history_v23_assets.py --guild 1544051203493601380 --file assets/history_v23/verified_import_ready.json --file path/to/restored_originals.json
```

檢查後才加上 `--apply`；整批最多 580 筆，所有跨品牌參照及素材編號會先檢查。

## Discord `/官方` 管理介面

進入 `/官方` → 內容建立 → **歷史履歷雙素材庫**，只允許群主管理：

- 「匯入已核准55筆」：一次性原子匯入，不再是只有 5 品牌＋5 代言。
- 「新增／核准素材」：人工建立單筆，檢查編號、品牌引用、內容來源及格式。
- 「批次匯入」：群主發送 JSON 訊息，或訊息附上 `.json` 檔案，再提供訊息連結；單檔最多 2 MB，最多 580 筆。候選稿會拒絕。
- 「搜尋素材」：依一般／路人池、WN/CN/HB/HE、編號或名稱查詢，唯讀分頁。
- 「修訂素材」：群主明確選取既有編號與完整內容；不同版本寫入 `rp_history_asset_revisions_v23`，角色既有歷史快照不變。
- 「停用／恢復」：不刪除原文、不改動歷史快照，變更記錄也保留在版本表。
- 「雙素材庫統計」：一般角色與路人獨立統計，彼此不交叉抽取。

## 安全規則

1. 素材要有正式可追溯原文；`content_origin=newly_rewritten_not_restored_original`、`import_ready=false`、未核准候選與缺原文索引**一律拒絕入庫**。
2. 同編號但文字／B 級／R 級／metadata 不同時，批次**整批取消**；不得靜默覆寫。
3. HE-0151～0230 的每筆歷史代言都必須連到已核准且啟用的 HB，並符合品牌 B 等級；停用中的既有品牌也不能透過「同批引用」繞過管制。
4. 手動修訂保留舊版本；若 HB 等級正被歷史代言引用，禁止直接變更該等級。
5. 一般角色池 `general` 與路人池 `passerby` 使用獨立複合主鍵；同一編號不代表同一筆內容。
6. 不會在素材匯入時自動抽取角色履歷或給任何金錢、數值與商品。

## 此版本未宣稱完成的項目

尚未取得 525 項既有核准原文，因此**完整的五筆歷史抽取仍可能因素材不足而等待補建**；沒有把後來的新編候選冒充原始核准文案。NPC 啟用、論壇通知、Discord／Railway 實機驗證並非本次素材庫交付範圍。

## 新增與覆蓋檔名

請開啟同一份交付內的 `HISTORY_V23_MATERIALS_CHANGED_FILES.txt`。它由實際對比原始 13:28 ZIP 產生，不需逐一猜測檔名。
