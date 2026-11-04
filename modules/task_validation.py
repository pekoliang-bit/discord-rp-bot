"""Official task-editor input validation. No unapproved reward may be granted here."""
from __future__ import annotations
import json
import re

KINDS={'newbie','daily','screen','variety','endorsement','limited','special'}
STATES={'draft','published','paused','archived'}
SUPPORTED_RULES={'occupations','min_age','min_cafe_level','min_attributes','max_age','allowed_character_ids'}
SUPPORTED_REWARDS={'coins','items','attributes'}

def parse_object(source, *, max_chars=6000):
    if isinstance(source,dict): return source
    if not isinstance(source,str) or len(source)>max_chars:raise ValueError('JSON 內容過長或格式錯誤')
    try: result=json.loads(source or '{}')
    except (ValueError,TypeError) as exc:raise ValueError('請輸入正確的 JSON 物件') from exc
    if not isinstance(result,dict):raise ValueError('設定內容必須是 JSON 物件')
    return result

def validate_rules(rules):
    rules=parse_object(rules)
    unknown=set(rules)-SUPPORTED_RULES
    if unknown:raise ValueError('不支援的接取條件：'+','.join(sorted(unknown)))
    for k in ('min_age','max_age','min_cafe_level'):
        if k in rules and (type(rules[k]) is not int or not 0<=rules[k]<=200):raise ValueError(k+' 必須是 0～200 的整數')
    if 'min_age' in rules and 'max_age' in rules and rules['min_age']>rules['max_age']:raise ValueError('年齡上下限矛盾')
    for k in ('occupations','allowed_character_ids'):
        if k in rules and (not isinstance(rules[k],list) or len(rules[k])>100):raise ValueError(k+' 必須是清單')
    if 'occupations' in rules and any(not isinstance(j,str) or not j.strip() for j in rules['occupations']):raise ValueError('職業名稱錯誤')
    if 'allowed_character_ids' in rules and any(type(x) is not int or x<=0 for x in rules['allowed_character_ids']):raise ValueError('角色編號錯誤')
    attrs=rules.get('min_attributes',{})
    if not isinstance(attrs,dict):raise ValueError('min_attributes 必須是物件')
    if any(not isinstance(k,str) or not k or type(v) is not int or not 0<=v<=10000000 for k,v in attrs.items()):raise ValueError('屬性條件格式錯誤')
    return rules

def validate_rewards(rewards):
    """Validation does not constitute disbursement authorization."""
    rewards=parse_object(rewards)
    unknown=set(rewards)-SUPPORTED_REWARDS
    if unknown:raise ValueError('未知獎勵類型：'+','.join(sorted(unknown)))
    if 'coins' in rewards and (type(rewards['coins']) is not int or not 0<=rewards['coins']<=100000000):raise ValueError('金幣獎勵格式錯誤')
    if 'items' in rewards:
        if not isinstance(rewards['items'],list) or len(rewards['items'])>20:raise ValueError('物品獎勵須為清單')
        for item in rewards['items']:
            if not isinstance(item,dict) or not isinstance(item.get('item_id'),(str,int)) or type(item.get('quantity')) is not int or not 1<=item['quantity']<=100:raise ValueError('物品獎勵需提供 item_id 和 quantity')
    attrs=rewards.get('attributes',{})
    if not isinstance(attrs,dict) or any(not isinstance(k,str) or not k or type(v) is not int or not 0<=v<=10000 for k,v in attrs.items()):raise ValueError('屬性獎勵格式錯誤')
    if any('影響' in k for k in attrs):raise ValueError('影響指數禁止透過一般任務獎勵設定')
    return rewards

def validate_report(report):
    report=parse_object(report)
    if set(report)-{'mode','channel_id'}:raise ValueError('不支援的回報欄位')
    if report.get('mode','manual') not in ('manual','automatic'):raise ValueError('回報方式錯誤')
    if report.get('mode','manual')=='manual' and not str(report.get('channel_id','')).isdigit():raise ValueError('人工回報必須指定討論串 ID')
    return report

def validate_definition(data, *, publish=False):
    if not isinstance(data,dict):raise ValueError('任務內容格式錯誤')
    if data.get('kind') not in KINDS:raise ValueError('任務分類錯誤')
    if not 1<=int(data.get('level',1))<=5:raise ValueError('通告等級必須為 1～5')
    for field in ('code','title'):
        value=str(data.get(field,'')).strip()
        if not value or len(value)>100:raise ValueError(field+' 不可為空或過長')
    if not re.fullmatch(r'[\w-]{1,60}',str(data['code']),re.UNICODE):raise ValueError('任務識別碼只能使用文字、數字、底線與連字號')
    for field in ('requirements','rewards','report'):
        data[field]=parse_object(data.get(field,{}))
    validate_rules(data['requirements']);validate_rewards(data['rewards'])
    if publish and data['kind']!='newbie':
        validate_report(data['report'])
        if data['kind'] in ('screen','variety','endorsement') and not str(data.get('subtype','')).strip():raise ValueError('通告必須選擇作品類型')
    if data.get('starts_at') and data.get('ends_at') and str(data['starts_at'])>=str(data['ends_at']):raise ValueError('開放時間不得晚於截止時間')
    return data
