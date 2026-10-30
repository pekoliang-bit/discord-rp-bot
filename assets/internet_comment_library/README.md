
# Discord RP 網路評論庫

版本：V1.0
狀態：評論資料層
初始目標：12 大分類，每類至少 80 則

## 一、目前資料

已審核通過：419 則
評論編號：C001～C420
下一個編號：C421

特殊紀錄：
- C074：已重新啟用
- C300：已排除，不得匯入
- 編號不可重複使用
- 原始評論不得擅自修改

## 二、資料夾結構

assets/
└── internet_comment_library/
    ├── README.md
    ├── manifest.json
    ├── merge_comments.py
    ├── comments_part_01.json
    ├── comments_part_02.json
    ├── comments_part_03.json
    ├── comments_part_04.json
    ├── comments_part_05.json
    ├── comments_part_06.json
    ├── comments_part_07.json
    ├── comments_part_08.json
    └── comments_part_09.json

## 三、12 大評論分類

01 日常生活
02 影視作品
03 演藝活動
04 商業活動
05 粉絲互動
06 感情關係／CP
07 爭議事件
08 公關回應
09 熱搜話題
10 營銷號
11 社會議題
12 特殊事件

每個分類的初始目標為至少 80 則。

80 則是最低建置數量，不是上限。

## 四、評論資料欄位

每則評論應包含：

- id：唯一評論編號
- number：數字編號
- batch：審核批次
- text：完整評論原文
- category_id：主分類編號
- category：主分類名稱
- subcategory：副分類
- speaker：模擬發言者類型
- emotion：評論情緒
- scene：適用情境
- trigger_limit：觸發限制
- source：來源
- canonical_id：主要模板編號
- requires_authorization：是否需要授權

原文中的標點、表情符號、變數及用語必須保留。

## 五、安裝程序

第一步：
將九個 comments_part_XX.json
放入 internet_comment_library 資料夾。

第二步：
確認 manifest.json 及
merge_comments.py 位於相同資料夾。

第三步：
在專案根目錄執行：

python assets/internet_comment_library/merge_comments.py

第四步：
成功產生 comments.json 後，
執行：

python -m scripts.import_comment_library

第五步：
檢查匯入結果及分類統計。

## 六、審核規則

1. 所有新增評論必須經群主審核。
2. 未核准評論不得加入正式抽取池。
3. 已核准評論不得擅自更改原文。
4. 已排除的評論不得重新自動啟用。
5. 同義評論可以保留不同編號。
6. 重複變體不得藉此增加抽取權重。
7. 群主可以決定新增、修改或排除模板。
8. 編號不得因刪除而重新排列。

## 七、觸發限制

評論必須符合對應事件。

生日評論：
需要角色生日或生日活動。

作品評論：
需要對應影視或音樂作品。

演出評論：
需要演出或活動情境。

爭議評論：
需要已存在的正式 RP 事件或傳聞。

商業評論：
需要品牌、商品或代言事件。

CP 評論：
不得自行建立角色感情關係。

角色本人發言：
必須由角色本人或授權帳號使用。

任何評論都不能單獨觸發：
- 角色屬性變化
- 金錢交易
- 粉絲數增加
- CP 關係變更
- 公關事件成立
- 虛構犯罪或死亡
- 熱搜排名變更

## 八、角色變數

ooo：主要角色
xxx：另一名不同角色
（暱稱）：角色暱稱
（粉絲名）：粉絲稱呼
（動物）：動物名稱
（工作人員）：對應工作人員

變數必須先通過驗證才能替換。

不得使用變數產生未授權的 Discord 提及。

斜線選項應依角色與語境選擇單一版本。

## 九、擴充方式

後續新增評論使用下一個未使用編號。

新增評論先列為待審核。

群主確認後才能加入 approved 清單。

可以新增 JSON 分檔，
並更新 manifest.json 的 parts 清單。

新增後必須重新合併及匯入。

## 十、資料安全

不得覆蓋原有玩家資料。

不得擅自刪除既有評論。

不得因更新模板而修改角色資料。

匯入操作必須防止重複。

正式部署前應備份 SQLite 資料庫。

本評論庫目前不負責 Discord 自動發文。

自動留言、情境驗證及權重抽取
應由後續模組實作。
