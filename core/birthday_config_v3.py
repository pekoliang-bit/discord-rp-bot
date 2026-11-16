"""獨立伺服器生日福利設定：預設採用已核准規則。"""
from __future__ import annotations

from core.world_interaction_rules import raw_world

DEFAULT = {
    'store_percent_off': 10,
    'store_hours': 72,
    'action_price': 350,
    'action_hours': 24,
    'location_hours': 72,
    'coins': 50000,
    'free_actions': 5,
    'free_points': 100,
    'birthday_tickets': 1,
    'venue_rules': {},  # 以正式地點 key 指定餐廳／課程等個別優惠
    'location_offers': {
        '餐廳':'買一送一', '咖啡廳':'指定飲品買一送一',
        '甜點店':'消費送生日甜點', '一般課程':'買二送一',
        '高級課程':'買五送一', '寵物訓練中心':'指定課程生日優惠',
    },
}


def settings(guild_id):
    value=raw_world(guild_id).get('birthday',{})
    if not isinstance(value,dict): value={}
    merged={**DEFAULT,**value}
    for key in ('store_percent_off','store_hours','action_price','action_hours',
                'location_hours','coins','free_actions','free_points','birthday_tickets'):
        if not isinstance(merged[key],int) or merged[key]<0:
            raise ValueError(f'生日世界觀設定不合法：{key}')
    if merged['store_percent_off']>100:
        raise ValueError('生日優惠不得超過100%')
    if not isinstance(merged['location_offers'],dict):
        raise ValueError('地點生日優惠格式錯誤')
    if not isinstance(merged['venue_rules'],dict):
        raise ValueError('個別地點生日福利設定必須是物件')
    allowed={'餐廳','咖啡廳','一般課程','高級課程','停用'}
    if any(not isinstance(k,str) or not k or v not in allowed for k,v in merged['venue_rules'].items()):
        raise ValueError('個別地點優惠必須為核准的福利類型')
    return merged
