"""醫院RP服務規則：純資料讀取與安全驗證；絕不代替醫療操作、扣款或擲骰。"""
from __future__ import annotations
import json
from pathlib import Path

_DATA=Path(__file__).resolve().parents[1]/"assets"/"action_locations"/"medical_default_pending.json"
BANNED_SERVICE_IDS=frozenset({'prenatal_care','childbirth','pregnancy_termination'})
COSMETIC_GROUPS=('小整形','大整形')

def load_services():
    data=json.loads(_DATA.read_text(encoding='utf-8'))
    validate_catalog(data)
    return data

def validate_catalog(data):
    if data.get('policy',{}).get('approved_for_execution') is not False:
        raise ValueError('醫院尚未核准正式執行')
    entries=data.get('services',[])
    ids=[x.get('id') for x in entries]
    if len(ids)!=len(set(ids)) or set(ids)&BANNED_SERVICE_IDS:
        raise ValueError('醫院服務重複或包含已取消服務')
    for e in entries:
        if e.get('approved') or e.get('enabled'):
            raise ValueError('醫療服務未核准，不可啟用')
        if not (e.get('requires_character_consent') and e.get('private_result')):
            raise ValueError('醫療服務必須私密且須當事角色同意')
        if e.get('group') in COSMETIC_GROUPS:
            p=e.get('price_coins'); bonus=e.get('success_bonus_points_proposed')
            if type(p)!=int or type(bonus)!=int or p<=0 or bonus<=0:
                raise ValueError('整形價格與成功獎勵必須是正整數草案')
            if e.get('outcome_chance') is not None or not e.get('failure_has_no_guaranteed_bonus'):
                raise ValueError('未核准成功機率或失敗保證獎勵')
    for group, low, high in [('小整形',100000,300000),('大整形',300000,1000000)]:
        levels=[e for e in entries if e.get('group')==group]
        if len(levels)<2 or any(not low<=e['price_coins']<high for e in levels):
            raise ValueError(f'{group}價格與級距不符')
        if any(a['success_bonus_points_proposed']>=b['success_bonus_points_proposed'] for a,b in zip(levels,levels[1:])):
            raise ValueError(f'{group}風險升高時成功獎勵必須提高')
    if max(e['success_bonus_points_proposed'] for e in entries if e.get('group')=='小整形')>=min(e['success_bonus_points_proposed'] for e in entries if e.get('group')=='大整形'):
        raise ValueError('大整形成功獎勵應高於小整形')
    return True

def available_services(*,owner_preview=False):
    data=load_services()
    if owner_preview:
        return list(data['services'])
    if not data['location'].get('service_enabled',False):
        return []
    return [e for e in data['services'] if e.get('approved') and e.get('enabled')]

def format_service(s):
    msg=s['name']
    if s.get('price_coins') is not None:
        msg+=f"｜價格草案 {s['price_coins']:,} 金幣"
    if s.get('risk_level'):
        msg+=f"｜風險 {s['risk_level']}｜成功時容貌 +{s['success_bonus_points_proposed']}（尚未核准）"
    return msg
