"""任務預設資料：僅安裝已確認的 16 項新手指令，其他待審核。"""
from __future__ import annotations
from modules.task_repository import initialize, publish_task
from modules.task_newbie import REQUIRED_COMMANDS

NAMES = {'查看':'認識自己','登記':'身分登記處','公司':'職場初體驗','商城':'購物天堂',
 '申請':'申請指南','銀行':'財富管理','陣營':'勢力版圖','網路':'初次上線',
 '劇情':'故事的開端','傳聞':'風聲四起','娛樂城':'燈火不夜城','惡意':'危機意識',
 '任務':'初次接觸','排行':'榮耀殿堂','總覽':'世界的全貌','抽獎':'幸運時刻'}

def seed_newbie(guild_id):
    initialize()
    # 不更動現有已編輯的任務，僅首次新增。
    from core.database import get_connection
    with get_connection() as db:
        existing={r['code'] for r in db.execute('SELECT code FROM rp_task_definitions WHERE guild_id=?',(str(guild_id),))}
    count=0
    for command in REQUIRED_COMMANDS:
        code='newbie_'+command
        if code in existing: continue
        publish_task(guild_id,code,'newbie',NAMES.get(command,command),
          description=f'在本人角色論壇「機器人使用」帖子成功開啟 /{command} 教學介面。',
          requirements={'event_key':'newbie:'+command},rewards={},report={'mode':'automatic'},status='published')
        count+=1
    return count
