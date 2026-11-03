Discord RP／網路 正式留言庫（960 則）

• 審核狀態：960 則群主核准，12 類各 80 則
• 編號：C001～C961；C300 已排除、C074 保留
• 新增：C421～C961 的文字以聊天審核版本為準；不能把舊的 comments_pending_C421-C961.json 當正式資料
• 正式資料：comments_part_01.json ～ comments_part_20.json + manifest.json
• 核對資料：../internet_comment_pending_review/approved_chat_texts.json

匯入

在專案根目錄先執行：

python -m scripts.check_comment_pipeline
python -m scripts.import_comment_library
python -m scripts.check_comment_database

注意：單純上傳 JSON 到 GitHub 並不會自動把留言加入已存在的 SQLite 資料庫。
請在持久儲存的正式環境執行匯入；如果使用 Railway，可在啟動指令前加入
python -m scripts.import_comment_library && python main.py（其中 python main.py
請依原本實際啟動方式調整）。此匯入具備冪等性，但每次都會寫入稽核紀錄。

安全規則

• 原有 C001～C420 的 419 則檔案不覆寫、不改寫
• 不允許新的未審核文字混進正式資料
• 已有同編號資料若文字或分類不一致，整批拒絕匯入並回滾
• 發言依事件核實、審核 ID 白名單、角色／帳號授權及爭議授權限制
• 允許 C421～C961 參與經過事件白名單驗證的評論抽取；不會自行建立事件、金錢或粉絲獎勵
• 匯入只操作評論相關資料表，不向 Discord 自動發文
• 正式資料庫請先備份

審核／資料路徑

assets/internet_comment_library/manifest.json 明確列出二十個分檔；
assets/internet_comment_pending_review/approved_chat_texts.json 保存新增 541 則核准文字的逐 ID 對照。
core.comment_library.seed_catalog() 會對照核准原文，任何一則不同均拒絕匯入。

分類

1. 日常生活
2. 影視作品
3. 演藝活動
4. 商業活動
5. 粉絲互動
6. 感情關係／CP
7. 爭議事件
8. 公關回應
9. 熱搜話題
10. 營銷號
11. 社會議題
12. 特殊事件

每類 80 則為首批最低目標，而非未來的上限。
