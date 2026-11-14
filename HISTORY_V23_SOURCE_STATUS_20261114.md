# 歷史履歷 V2.3｜11/14 素材來源檢查與操作

來源基準：`discord-rp-bot-main(20261114-091943).zip`。本次不更動 NPC 初始屬性、金錢公式、玩家資料或新手禮包規則。

## 先說明：580 筆編號不等於 580 筆已核准原文入庫

歷史履歷總規格分成 WN 200、CN 100、HB 50、HE 230，總計 580 個素材編號。最新 ZIP 只實際附有 `assets/history_v23/verified_five.json`，即 HB-0046～HB-0050 五筆，及 HE-0151、0166、0186、0206、0221 五筆。

本次已把全部 580 個編號列成一張**缺件總帳**，真正可匯入的維持 10 筆。其他 570 筆沒有原文，`title=null` 且 `import_ready=false`，禁止由程式標記為核准、啟用或拿來抽取。這避免把編號清單誤認為素材已匯入。

| 類別 | 編號 | 目標 | 已核實全文 | 缺原文 |
| --- | --- | ---: | ---: | ---: |
| 作品名稱 | WN-0001～0200 | 200 | 0 | 200 |
| 劇中角色姓名 | CN-0001～0100 | 100 | 0 | 100 |
| 歷史品牌 | HB-0001～0050 | 50 | 5 | 45 |
| 正面事件 | HE-0001～0075 | 75 | 0 | 75 |
| 負面事件 | HE-0076～0150 | 75 | 0 | 75 |
| 歷史代言 | HE-0151～0230 | 80 | 5 | 75 |
| **總計** |  | **580** | **10** | **570** |

**來源限制：** 上述「缺原文」並不是要使用者重寫規則，而是原始核准的逐條文字沒有出現在本次 ZIP。不能從數量及編號反推原文。取得原始核准內容後，將其放入有標題的 JSON，再使用下述匯入程式；不要將 `catalog_580_inventory.json` 當成可匯入資料。

## 新增素材檔

- `assets/history_v23/catalog_580_inventory.json`：580 筆編號總表（原文缺失者留空，不得抽取）。
- `assets/history_v23/WN_0001_0200_inventory.json`：200 筆作品編號。
- `assets/history_v23/CN_0001_0100_inventory.json`：100 筆角色名編號。
- `assets/history_v23/HB_0001_0050_inventory.json`：50 筆品牌編號。
- `assets/history_v23/HE_0001_0075_inventory.json`：75 筆正面事件編號。
- `assets/history_v23/HE_0076_0150_inventory.json`：75 筆負面事件編號。
- `assets/history_v23/HE_0151_0230_inventory.json`：80 筆歷史代言編號。
- `assets/history_v23/verified_import_ready.json`：10 筆確實有原文的核准素材。
- `assets/history_v23/IMPORT_AUDIT.json`：逐類完成數量與缺件報表。

## 如何執行

在專案根目錄：

```bash
python tools/build_history_v23_catalog.py
python tests/verify_history_v23.py
python tests/verify_history_v23_assets.py
```

從已核實的原文檔 dry-run（**預設不寫入資料庫**）：

```bash
python tools/import_history_v23_assets.py --guild 1544051203493601380
```

在**已備份而且經過確認的資料庫環境**，確定要寫入時再執行：

```bash
python tools/import_history_v23_assets.py --guild 1544051203493601380 --apply
```

- 程式僅新增／確認核准素材，不會自動抽取歷史、不發錢、不改 NPC 面板。
- 已有相同素材的重新執行應冪等處理；若有同 ID 不同原文，會拒絕覆蓋。
- 歷史代言 HE 必須包含對應核准品牌 `hb_id` 且 B 級相同；沒有對應品牌就拒絕。
- 未核准的 570 筆原文待補內容不可使用 `--apply` 強行匯入。

## 原始 ZIP 啟動問題

本次修正 `modules/character_sheet_registration.py` 第一行原本被錯誤拼接兩次的 docstring；完成後專案 Python 語法掃描通過。未變更此模組的其他功能內容。

## 尚未驗證與目前不可宣稱完成的部分

- 不具備核准原文的 570 項內容；真正可抽取的五筆歷史履歷仍可能因缺作品／經歷而待補。
- Discord 實際互動、論壇通知、Railway 部署及初始福利沒有在本次容器中實機驗證。
- NPC 初始數值仍應使用另一個對話的版本，**不要在合併前用本套件覆蓋正式數值版本**。
