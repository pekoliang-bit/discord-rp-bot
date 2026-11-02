"""排行榜 V11 目錄。純設定，不註冊 Discord 指令。"""
BOARDS = [
('impact','總榜（影響指數）'),('wealth','金錢'),('heat','流量熱度'),
('commercial','商業價值'),('public_favor','路人緣'),('pr','公關'),
('appearance','容貌'),('intellect','才智'),('main_skill','主攻榜'),
('acting','演繹'),('dance','舞蹈'),('sing','歌唱'),('hosting','主持'),
('variety','綜藝'),('talent','個人才藝'),('fans','粉絲'),
('antis','黑紅'),('awareness','大眾認知'),('cp','最佳CP'),
('works','作品數量'),('endorsements','代言數量'),
('industry','產業經營（公司、品牌）'),('groups','集團競逐'),
('shops','商鋪排名'),('factions','陣營'),('social','網路影響'),
('newcomer','新人獎'),('awards','獎項排名'),
]
BOARD_NAMES = dict(BOARDS)
# 僅下列欄位已由現有 core/database.py 的 attributes / character_skills 明確支持。
# 其他榜不猜測欄位；介面會標示待接資料源。
ATTRIBUTE_ALIASES = {
 'public_favor':('路人緣',), 'pr':('公關',),
 'appearance':('容貌',), 'intellect':('才智','才華'),
 'commercial':('商業價值',), 'heat':('熱度','流量熱度'),
 'awareness':('大眾認知度','大眾認知'),
}
SKILL_ALIASES = {
 'acting':('演繹','演技'), 'dance':('舞蹈',), 'sing':('歌唱',),
 'hosting':('主持',), 'variety':('綜藝',), 'talent':('個人才藝',),
}
