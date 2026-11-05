# 代言通告｜商業短工首批（2026-11-06）

基於使用者上傳之 `discord-rp-bot-main(20261105-131657).zip` 修改。

## 本次開放

- 20 類低門檻一次性商演、廣告與展覽活動，均歸入既有 `/任務 → 代言通告`，**不新增頂層斜線指令**。
- 單一正式屬性要求 100–300；單一相同屬性成長 +5–15，無自由點數、影響指數或正式代言頭銜；另有金幣 1,200–4,200。
- 無咖位門檻；保留個別工作適用的職業白名單。
- 每個單次通告完成須由角色本人在「劇情討論」帖子回報，沿用官方審核與安全獎勵結算。
- 同類工作完成結算後可隔 12 / 24 / 36 **現實小時**再接取；沒有完成的任務不得重複接取。
- 首次使用 `/任務` 自動補齊缺少的預設資料；舊資料不覆蓋、不重設。

## 與正式代言的隔離

原先 14 筆品牌代言名單及已指定代言人**沒有**轉成普通反覆接取的短工。正式品牌代言依既有公司的頭銜、申請、合約及品牌規則處理；本次只是增加沒有品牌頭銜的一次性商業收入來源。

## 本次變動檔案

覆蓋：
- `modules/task_conditions.py`
- `modules/task_service.py`
- `modules/z_tasks_center.py`

新增：
- `modules/task_commercial_seed.py`
- `scripts/test_commercial_basic_playable.py`
- `COMMERCIAL_BASIC_BATCH_20261106.md`

## 驗證

`PYTHONPATH=. python scripts/test_commercial_basic_playable.py` 測試通過；既有 `test_task_end_to_end.py`、`test_variety_playable.py`、`test_screen_seed_playable.py` 通過。這些是本機測試，未進行 Discord 線上驗收。

## 尚未實作／須另階段

- 任何短工的真實拍攝／表演檔期自動預約及跨通告撞期檢查（目前整體檔期尚未接線）。
- 與品牌公司代言合約同步、正式代言曝光或長期分潤；本次商業短工不授予品牌頭銜。
- 短工間的「全類型共享」收益上限（目前僅逐項獨立冷卻）。
