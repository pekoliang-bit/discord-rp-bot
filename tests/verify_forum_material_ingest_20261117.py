"""離線驗證帖子對應、欄位解析、同通告多職位與原生素材庫隔離。"""
from __future__ import annotations
import os
import tempfile

with tempfile.TemporaryDirectory() as d:
    os.environ['DATABASE_PATH']=os.path.join(d,'bot.sqlite3')
    from core.database import init_db,get_connection
    from core import forum_material_ingest_v1 as m
    init_db();m.setup()
    assert len(m.THREADS)==17 and len(set(m.THREADS))==17
    for k in ('male_adult','female_adult','male_minor','female_minor'):
        assert m.parse('',k,['https://cdn.discordapp.com/attachments/a/b.jpg'])['urls']
    p=m.parse('國籍：日本\n姓氏：佐藤','surname')
    assert m.commit_material(m.GUILD_ID,'101','1572146261023526983','1','surname',p)[0]=='imported'
    assert m.commit_material(m.GUILD_ID,'101','1572146261023526983','1','surname',p)[0]=='duplicate'
    with get_connection() as db:
        assert db.execute("select value from extra_name_pool where kind='surname' AND value='佐藤'").fetchone()[0]=='佐藤'
    ad='''廣告名稱：霧港廣告\n廣告類型：形象影片\n拍攝內容：劇情式廣告\n所需檔期：12/1~12/3\n招募截止：11/25\n\n【招募角色】\n1.\n職位：女主角\n人數：1\n所需咖位：LV10以上\n所需數值：容貌≥120、演繹≥100\n酬勞：50000金幣\n\n2.\n職位：男配角\n人數：2\n所需咖位：不限\n所需數值：演繹≥60\n酬勞：10000金幣'''
    p=m.parse(ad,'advertising');assert len(p['slots'])==2
    assert m.commit_material(m.GUILD_ID,'102','157217752759678167','1','advertising',p)[0]=='draft'
    with get_connection() as db:
        tasks=db.execute('select title,status,requirements_json from rp_task_definitions').fetchall()
        assert len(tasks)==2 and all(t['status']=='draft' for t in tasks),tasks
    # 代言舊模板使用「合作檔期」應能收件
    p=m.parse('品牌名稱：品牌甲\n代言頭銜：T4 品牌大使\n代言費用：5000金幣\n合作檔期：30天\n招募截止：2026/12/10','endorsement')
    assert len(p['slots'])==1
    # 歷史代言只需四個欄位，不擅自給正式素材庫 B 級
    p=m.parse('品牌名稱：A\n代言內容：B\n頭銜：T4\n稀有度：R3','HB')
    assert m.commit_material(m.GUILD_ID,'103','1572148643828604948','1','HB',p)[0]=='pending'
    print('論壇素材基本測試通過')
