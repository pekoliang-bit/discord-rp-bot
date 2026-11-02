
"""路人角色正式初始配點與資歷強化（一般池、藝能池分開）。"""
from __future__ import annotations

import random

from core.attribute_validation import calculate_values
from modules.attributes import default_schema
from modules.extra_character_rules import (
    SPLITS, choose_type, sample_pool, sample_resources
)

MAIN_SKILLS = ("演繹", "舞蹈", "歌唱", "綜藝", "主持")

TRACKS = {
    "演員": "演繹",
    "舞者": "舞蹈",
    "歌手": "歌唱",
    "偶像": "歌唱",
    "模特兒": "演繹",
    "導演": "演繹",
    "編劇": "演繹",
    "主持人": "主持",
    "配音員": "演繹",
    "編舞師": "舞蹈",
    "製作人": "演繹",
    "綜藝藝人": "綜藝",
    "網紅": "綜藝",
    "經紀人": "主持",
}

JOB_SECONDARY = {
    "演員": ("容貌", "才智", "主持"),
    "歌手": ("個人才藝", "舞蹈", "演繹"),
    "舞者": ("個人才藝", "演繹", "容貌"),
    "偶像": ("舞蹈", "演繹", "綜藝"),
    "模特兒": ("容貌", "演繹", "舞蹈"),
    "配音員": ("演繹", "個人才藝", "才智"),
    "主持人": ("綜藝", "才智", "演繹"),
    "綜藝藝人": ("主持", "演繹", "個人才藝"),
    "網紅": ("主持", "容貌", "個人才藝"),
    "導演": ("才智", "演繹", "個人才藝"),
    "編劇": ("才智", "演繹", "個人才藝"),
    "編舞師": ("舞蹈", "個人才藝", "才智"),
    "製作人": ("才智", "個人才藝", "演繹"),
    "經紀人": ("才智", "主持", "綜藝"),
}


def _allocate_initial(keys, total, caps):
    """在正式創角上限內精確分完點數。"""
    points = {name: 0 for name in keys}
    if sum(caps.values()) < total:
        raise ValueError("創角配點容量不足")

    while total:
        available = [
            k for k in keys if points[k] < caps[k]
        ]
        key = random.choice(available)
        points[key] += 1
        total -= 1

    return points


def _add_experience(
    values, keys, amount, main, job, ability_type
):
    """資歷點數僅能加在所屬屬性池，且不碰影響指數。"""
    if amount <= 0 or not keys:
        return

    major = [main] if main in keys else []
    desired = JOB_SECONDARY.get(job, ())
    secondary = [
        k for k in desired
        if k in keys and k not in major
    ]

    if not secondary:
        secondary = [
            k for k in keys if k not in major
        ][:3]

    if not major:
        major = (
            [secondary.pop(0)]
            if secondary else [keys[0]]
        )

    other = [
        k for k in keys
        if k not in major and k not in secondary
    ]

    groups = (major, secondary, other)
    proportions = SPLITS[ability_type]
    portions = [
        int(amount * proportions[0]),
        int(amount * proportions[1]),
    ]
    portions.append(amount - sum(portions))

    for group, portion in zip(groups, portions):
        targets = group or other or secondary or major
        for _ in range(portion):
            values[random.choice(targets)] += 1


def generate_profile(age, job, level, gender):
    age = int(age)
    level = int(level)

    if age < 5 or not 1 <= level <= 9:
        raise ValueError(
            "路人初始咖位需為LV1～LV9，"
            "資歷不得大於年齡減5"
        )

    schema = default_schema()
    general_keys = list(schema["general"])
    talent_keys = list(schema["talent"])
    main = TRACKS.get(job) or random.choice(MAIN_SKILLS)
    years = random.randint(0, age - 5)
    ability_type = choose_type(job)

    general = _allocate_initial(
        [
            k for k in general_keys
            if k != "影響指數"
        ],
        500,
        {
            k: 130 for k in general_keys
            if k != "影響指數"
        },
    )
    general["影響指數"] = 0

    talent = _allocate_initial(
        talent_keys,
        500,
        {
            k: (115 if k == main else 130)
            for k in talent_keys
        },
    )

    # 由正式驗證器一次計入基礎值與主攻加成。
    values = dict(
        calculate_values(schema, main, general, talent)
    )

    gen_target, talent_target = sample_pool(
        years, level
    )

    _add_experience(
        values,
        [
            k for k in general_keys
            if k != "影響指數"
        ],
        max(0, gen_target - 500),
        main,
        job,
        ability_type,
    )
    _add_experience(
        values,
        talent_keys,
        max(0, talent_target - 500),
        main,
        job,
        ability_type,
    )

    resources = sample_resources(level)
    values["影響指數"] = 20

    # 熱度與商業價值本身就是正式一般屬性。
    values["流量熱度"] = int(resources["heat"])
    values["商業價值"] = int(resources["commercial"])

    return {
        "gender": "男" if gender == "male" else "女",
        "experience_years": years,
        "main_track": main,
        "secondary_track": "",
        "ability_type": ability_type,
        "initial_general_points": general,
        "initial_talent_points": talent,
        "extra_initial_attributes": values,
        "initial_resources": resources,
        "extra_initial_coins": int(resources["coins"]),
        "extra_initial_fans": int(resources["fans"]),
    }


def write_profile(db, guild_id, character_id, profile):
    for name, value in profile[
        "extra_initial_attributes"
    ].items():
        db.execute(
            """INSERT INTO attributes
               (guild_id,character_id,name,value)
               VALUES(?,?,?,?)
               ON CONFLICT(guild_id,character_id,name)
               DO UPDATE SET value=excluded.value""",
            (
                str(guild_id),
                int(character_id),
                name,
                int(value),
            ),
        )
