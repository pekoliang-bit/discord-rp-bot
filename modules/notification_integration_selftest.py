"""執行 `python -m modules.notification_integration_selftest` 測試純公告格式與去重。"""
from __future__ import annotations
from modules.notification_categories import CATEGORIES
from modules.notification_announcement_format import format_announcement

def run():
    assert [x[0] for x in CATEGORIES] == ['story','system','activity','character','shop','company','faction','world','other']
    rows=[{'category':'system','action':'新增','title':'測試','character_name':None},
          {'category':'story','action':'新增','title':'測試劇情','character_name':'角色甲'},
          {'category':'story','action':'新增','title':'測試劇情二','character_name':'角色甲'}]
    body,active=format_announcement(rows)
    assert active==['story','system'], active
    assert '角色甲：新增 2 條' in body, body
    assert body.index('攻略劇情') < body.index('系統增加')
    assert '分類按鈕' in body
    return '公告格式及九類排序測試通過'

if __name__=='__main__':
    print(run())
