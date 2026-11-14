"""恢復先前已核准的45筆歷史品牌，按原名原級別直接納入正式素材。
"""
from __future__ import annotations
import json
from pathlib import Path

# (編號, 品牌名稱, 原核准品牌層級, 分類)
RAW = [
(1,'Mellow Day 柔日','B1','美妝護膚'),
(2,'PUREMINT 薄荷日記','B1','美妝護膚'),
(3,'LUMIYA 露米雅','B2','美妝護膚'),
(4,'Bloom & Dew 花露集','B2','美妝護膚'),
(5,'SOLENNE 索蘭','B3','美妝護膚'),
(6,'AMBER VEIL 琥珀紗','B3','美妝護膚'),
(7,'ÉTOILE SKIN 星緹','B3','美妝護膚'),
(8,'VELORIA 薇洛莉亞','B4','美妝護膚'),
(9,'AURELLE 奧蕾爾','B4','美妝護膚'),
(10,'MAISON CÉLESTE 天穹之庭','B5','美妝護膚'),
(11,'DAYFOLD 日褶','B1','服飾時尚'),
(12,'COTTON MOON 棉月','B1','服飾時尚'),
(13,'LINE & LEAF 線葉','B2','服飾時尚'),
(14,'VIVID FORM 鮮形','B2','服飾時尚'),
(15,'MONAIRE 莫奈爾','B3','服飾時尚'),
(16,'ATELIER NOIR 黑色工坊','B3','服飾時尚'),
(17,'SEVÉRA 賽薇拉','B3','服飾時尚'),
(18,'VELVET HOUR 絲絨時刻','B4','服飾時尚'),
(19,'MAISON ARDENT 熾焰之屋','B5','服飾時尚'),
(20,'LUMEN RING 光環','B1','珠寶配飾'),
(21,'SILVER TALE 銀色寓言','B2','珠寶配飾'),
(22,'ORBIS JEWEL 星軌珠寶','B3','珠寶配飾'),
(23,'VÉRANE 維蘭','B3','珠寶配飾'),
(24,'ÉCLAT NOIR 黑曜之光','B4','珠寶配飾'),
(25,"MAISON D'OR 金曜之庭",'B5','珠寶配飾'),
(26,'BYTEPOP 位元泡泡','B1','數碼科技'),
(27,'PIXEL DAY 像素日常','B2','數碼科技'),
(28,'SONOVA 聲諾','B2','數碼科技'),
(29,'NEBULA CORE 星核','B3','數碼科技'),
(30,'VECTRA 維科特','B3','數碼科技'),
(31,'AURIX 奧瑞克斯','B4','數碼科技'),
(32,'NEXION 領界','B5','數碼科技'),
(33,'SUNNY BITE 晴日小食','B1','食品飲料'),
(34,'POPCORN PLANET 爆米星球','B1','食品飲料'),
(35,'FRESH HOUR 鮮時光','B2','食品飲料'),
(36,'MILK CLOUD 奶雲研究所','B2','食品飲料'),
(37,'MIDNIGHT CUP 午夜杯','B3','食品飲料'),
(38,'HONEY GROVE 蜜森','B3','食品飲料'),
(39,"L'ATELIER DU THÉ 茶藝之所",'B4','食品飲料'),
(40,'MAISON SUCRÉE 糖霜之庭','B5','食品飲料'),
(41,'CLEANO 潔樂','B1','家居生活'),
(42,'SOFT NEST 柔巢','B2','家居生活'),
(43,'LITTLE HABIT 日常小事','B2','家居生活'),
(44,'HOME & HAZE 霧居','B3','家居生活'),
(45,'LIVING FORM 生活形制','B4','家居生活'),
]

def main():
    assert len(RAW)==45 and [x[0] for x in RAW]==list(range(1,46))
    folder=Path(__file__).resolve().parents[1]/'assets'/'history_v23'
    output=[{'asset_id':f'HB-{i:04d}','title':name,'tier':tier,'meta':{'industry':category},
             'pool':'general','status':'previously_approved','approved':True,'import_ready':True}
            for i,name,tier,category in RAW]
    (folder/'HB_0001_0045_approved.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('已恢復45個先前正式核准的歷史品牌；不需再次審核。')

if __name__=='__main__':main()
