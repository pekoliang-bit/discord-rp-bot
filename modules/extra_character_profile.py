
"""路人屬性：正式創角雙500 + 合法出道資歷加成；影響指數不增加。"""
from __future__ import annotations
import random
from core.attribute_validation import calculate_values
from modules.attributes import default_schema
from modules.extra_character_rules import (
    sample_pool, sample_resources, choose_type, SPLITS
)

MAIN_SKILLS = ('演繹', '舞蹈', '歌唱', '綜藝', '主持')

TRACKS = {
    '演員': '演繹',
    '舞者': '舞蹈',
    '歌手': '歌唱',
    '偶像': '歌唱',
    '模特兒': '演繹',
    '導演': '演繹',
    '編劇': '演繹',
    '主持人': '主持',
    '配音員': '演繹',
    '編舞師': '舞蹈',
    '製作人': '演繹',
    '綜藝藝人': '綜藝',
    '網紅': '綜藝',
}

def distribute(keys, total, cap, weight=None):
    out = {k: 0 for k in keys}
    while total > 0:
        choices = [
            k for k in keys
            if out[k] < cap.get(k, 150)
        ]
        if not choices:
            raise ValueError('屬性配點容量不足')
        weights = [
            weight.get(k, 1) if weight else 1
            for k in choices
        ]
        chosen = random.choices(choices, weights=weights)[0]
        take = min(
            total,
            random.randint(1, 15),
            cap.get(chosen, 150) - out[chosen]
        )
        out[chosen] += take
        total -= take
    return out

def bonus(values, total, main, kind):
    if total <= 0:
        return values
    keys = [k for k in values if k != '影響指數']
    random.shuffle(keys)
    major = [main] if main in keys else [keys[0]]
    secondary = [
        k for k in keys if k not in major
    ][:min(3, max(1, len(keys) - 1))]
    other = [
        k for k in keys
        if k not in major and k not in secondary
    ]
    groups = (major, secondary, other)
    amounts = [
        int(total * SPLITS[kind][0]),
        int(total * SPLITS[kind][1])
    ]
    amounts.append(total - sum(amounts))
    for group, amount in zip(groups, amounts):
        targets = group or (secondary or major)
        for _ in range(amount):
            values[random.choice(targets)] += 1
    return values

def generate_profile(age, job, level, gender):
    schema = default_schema()
    main = TRACKS.get(job) or random.choice(MAIN_SKILLS)
    years = random.randint(0, max(0, int(age) - 5))
    kind = choose_type(job)

    gen = [
        k for k in schema['general']
        if k != '影響指數'
    ]
    tal = schema['talent']

    # 四項一般屬性各最多130點，總容量520。
    general = distribute(
        gen, 500, {k: 130 for k in gen}
    )
    general['影響指數'] = 0

    talent = distribute(
        tal, 500, {
            k: (115 if k == main else 130)
            for k in tal
        }
    )

    base = calculate_values(schema, main, general, talent)
    general_total, talent_total = sample_pool(years, level)
    resources = sample_resources(level)

    result = bonus(
        dict(base),
        max(0, general_total - 500),
        main, kind
    )
    result = bonus(
        result,
        max(0, talent_total - 500),
        main, kind
    )
    result['影響指數'] = 20
    result['流量熱度'] = resources['heat']
    result['商業價值'] = resources['commercial']

    return {
        'gender': '男' if gender == 'male' else '女',
        'experience_years': years,
        'main_track': main,
        'secondary_track': '',
        'ability_type': kind,
        'initial_general_points': general,
        'initial_talent_points': talent,
        'extra_initial_attributes': result,
        'initial_resources': resources,
        'extra_initial_coins': resources['coins'],
        'extra_initial_fans': resources['fans'],
    }

def write_profile(db, guild_id, cid, profile):
    for name, value in profile['extra_initial_attributes'].items():
        db.execute(
            '''INSERT INTO attributes
               (guild_id,character_id,name,value)
               VALUES(?,?,?,?)
               ON CONFLICT(guild_id,character_id,name)
               DO UPDATE SET value=excluded.value''',
            (str(guild_id), int(cid), name, int(value))
        )
