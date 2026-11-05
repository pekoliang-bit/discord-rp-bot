
# /網路：直播到期交易防護（2026-11-05）

基礎：discord-rp-bot-main(20261105-050546).zip。

## 本次修改

- `core/live_commerce.py`：直播帶貨提案、購買意向在交易中檢查直播期限。
- `core/live_checkout.py`：真實帶貨下單在金流、背包及庫存更新之前檢查直播期限；既有重複請求仍可讀取舊訂單，不重複扣款。
- `core/live_pk.py`：PK 邀請與接受增加到期檢查，待處理邀請列表排除過期直播。
- `tests/test_live_expiration_guards.py`：新增直播到期防護回歸測試。
- `core/live_social.py`：此基礎版本已有到期防護，本次不需覆蓋。

## 正式規則

1. 直播禮物收入平台抽成 20%，角色及其他分潤方合計實得 80%。
2. 帶貨佣金平台抽成 20%，並非直接抽取商品成交額的 20%。
3. 非網紅職業最近連續 7 天直播或直播帶貨合計超過 5 場，從第 6 場開始記錄負面風險。
4. 負面風險的具體數值影響尚未核准，不得自動扣除未設定的角色數值。

## 待完成

- 正式負面效果與傳聞、公關系統連動。
- 會員直播資格與官方直播權限核對。
- 非官方商城及玩家店舖帶貨適配器。
- 社群留言、網路輿論、全員群與陣營頻道連動。
- 全系統 Discord 實機驗證。

## 測試指令

python -m unittest tests.test_live_expiration_guards tests.test_live_stream_core tests.test_live_income_frequency

本地相關測試曾回報 12 項通過，正式部署及 Discord 端到端驗證仍待進行。
