"""寵物中心 V2.1 候選資料。售價/商品等級/隱藏效果均是可調測試值，未最終核准。"""
from __future__ import annotations

BREEDS = [('v21:cat:01', '貓', '英國短毛貓', 220000, 1), ('v21:cat:02', '貓', '美國短毛貓', 180000, 1), ('v21:cat:03', '貓', '布偶貓', 380000, 1), ('v21:cat:04', '貓', '緬因貓', 480000, 1), ('v21:cat:05', '貓', '波斯貓', 260000, 1), ('v21:cat:06', '貓', '暹羅貓', 200000, 1), ('v21:cat:07', '貓', '曼赤肯貓', 350000, 1), ('v21:cat:08', '貓', '俄羅斯藍貓', 300000, 1), ('v21:dog:01', '狗', '柴犬', 240000, 1), ('v21:dog:02', '狗', '柯基', 300000, 1), ('v21:dog:03', '狗', '貴賓犬', 220000, 1), ('v21:dog:04', '狗', '博美犬', 280000, 1), ('v21:dog:05', '狗', '黃金獵犬', 350000, 1), ('v21:dog:06', '狗', '哈士奇', 320000, 1), ('v21:dog:07', '狗', '薩摩耶', 480000, 1), ('v21:dog:08', '狗', '邊境牧羊犬', 380000, 1), ('v21:dog:09', '狗', '德國牧羊犬', 400000, 1), ('v21:dog:10', '狗', '秋田犬', 420000, 1), ('v21:parrot:01', '鸚鵡', '虎皮鸚鵡', 120000, 1), ('v21:parrot:02', '鸚鵡', '玄鳳鸚鵡', 180000, 1), ('v21:parrot:03', '鸚鵡', '牡丹鸚鵡', 220000, 1), ('v21:parrot:04', '鸚鵡', '和尚鸚鵡', 300000, 0), ('v21:parrot:05', '鸚鵡', '綠頰錐尾鸚鵡', 380000, 0), ('v21:parrot:06', '鸚鵡', '非洲灰鸚鵡', 680000, 0)]
PAGES = {'貓': [('cage', '籠子', False), ('bed', '窩', True), ('toilet', '廁所', True), ('carrier', '航空箱／外出籠', True), ('bowl', '食盆水碗', True), ('leash', '牽繩', False), ('toy', '玩具', False), ('feed', '飼料', True), ('treat', '零食', False), ('supplement', '健康食品', False), ('daily', '其他日常用品', False)], '狗': [('cage', '籠子／圍欄', False), ('bed', '窩', True), ('toilet', '廁所', True), ('carrier', '航空箱／外出籠', True), ('bowl', '食盆水碗', True), ('leash', '牽繩', True), ('toy', '玩具', False), ('feed', '飼料', True), ('treat', '零食', False), ('supplement', '健康食品', False), ('daily', '其他日常用品', False)], '鸚鵡': [('cage', '鳥籠', True), ('bed', '窩／棲木', True), ('toilet', '籠底清潔用品', True), ('carrier', '航空箱／外出鳥籠', False), ('bowl', '食盆水碗', True), ('leash', '飛行背帶', False), ('toy', '玩具', False), ('feed', '飼料', True), ('treat', '零食', False), ('supplement', '健康食品', False), ('daily', '其他日常用品', False)]}
LONG_HAIR = {'布偶貓', '緬因貓', '波斯貓', '博美犬', '黃金獵犬', '薩摩耶'}
QUALITY = (('basic', '基礎款', 1), ('premium', '高級款', 2), ('luxury', '頂級款', 4))
BASE_PRICE = {'cage': 1500, 'bed': 1200, 'toilet': 900, 'carrier': 2500, 'bowl': 600,
              'leash': 1000, 'toy': 650, 'feed': 700, 'treat': 450,
              'supplement': 1200, 'daily': 500, 'groom': 950}
CATEGORY = {'feed':'飼料','treat':'零食','supplement':'健康食品','toy':'玩具'}


def wizard_pages(species, breed_name=''):
    if species not in PAGES: return []
    rows = list(PAGES[species])
    if breed_name in LONG_HAIR:
        rows.insert(next(i for i, p in enumerate(rows) if p[0] == 'feed'), ('groom', '梳理用品', True))
    return rows


def item_templates():
    """回傳有物種適配與品質分層的用品預設值。"""
    for species, pages in PAGES.items():
        for group, label, required in pages:
            for quality, quality_label, mult in QUALITY:
                code = f'v21:{ {"貓":"cat", "狗":"dog", "鸚鵡":"parrot"}[species]}:{group}:{quality}'
                yield {'code':code,'species':species,'group':group,'label':f'{label}・{quality_label}',
                       'quality':quality,'price':BASE_PRICE[group]*mult,'required':required,
                       'category':CATEGORY.get(group,'日常'),'permanent':group not in ('feed','treat','supplement'),
                       'hidden_health':int(mult-1) if group in ('feed','supplement','bed') else 0,
                       'hidden_bond':int(mult-1) if group in ('toy','treat','bed') else 0}
        for quality, quality_label, mult in QUALITY:
            group='groom';code=f'v21:{ {"貓":"cat", "狗":"dog", "鸚鵡":"parrot"}[species]}:{group}:{quality}'
            yield {'code':code,'species':species,'group':group,'label':f'梳理用品・{quality_label}',
                   'quality':quality,'price':BASE_PRICE[group]*mult,'required':False,
                   'category':'日常','permanent':True,'hidden_health':mult-1,'hidden_bond':0}
