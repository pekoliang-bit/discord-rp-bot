
"""Discord RP 虛構網名產生器。

不使用真實玩家身分。
不連線 Discord、不寫入資料庫。
網名只作為 RP 模擬社群中的展示名稱。
"""

from __future__ import annotations

import random
import re
from typing import Collection


ACCOUNT_TYPES = {
    "passerby": "普通路人",
    "fan": "普通粉絲",
    "superfan": "大粉",
    "fansite": "站姐／站哥",
    "solo_fan": "唯粉",
    "anti": "黑粉／酸民",
    "cp_fan": "CP粉",
    "gossip": "吃瓜群眾",
}

PREFIXES = {
    "passerby": (
        "路過的", "今天也在", "一隻", "隔壁",
        "正在摸魚的", "睡不醒的", "不想上班的",
    ),
    "fan": (
        "每天都想見", "心動收藏家", "宇宙第一",
        "為你亮燈", "追星日記", "永遠偏愛",
    ),
    "superfan": (
        "首席應援", "星光守護", "全年無休",
        "專屬補給站", "一直都在", "超級護衛隊",
    ),
    "fansite": (
        "月光底片", "星軌映像", "藍調快門",
        "午後取景", "銀河焦距", "底片日記",
    ),
    "solo_fan": (
        "只看", "唯一偏愛", "獨家珍藏",
        "一心一意", "本命守護", "唯愛",
    ),
    "anti": (
        "冷靜觀察", "不吐不快", "路過但忍不住",
        "專業挑刺", "理性質疑", "今天也想問",
    ),
    "cp_fan": (
        "雙人星軌", "嗑糖日記", "今天有糖嗎",
        "同框觀察室", "心動雙人組", "糖分超標",
    ),
    "gossip": (
        "瓜田巡邏", "吃瓜前線", "熱搜觀察",
        "八卦顯微鏡", "路人情報局", "瓜田居民",
    ),
}

SUFFIXES = (
    "小貓", "雲朵", "檸檬茶", "夜貓子",
    "碎碎念", "小行星", "便利貼",
    "咖啡杯", "觀察員", "泡泡",
    "備忘錄", "不熬夜", "日記本",
)

FANSITE_SUFFIXES = (
    "PHOTO", "FILM", "STUDIO", "ARCHIVE",
    "FOCUS", "CAM", "GALLERY",
)

FORBIDDEN_TERMS = (
    "@everyone",
    "@here",
    "discord.gg/",
)


def normalize_username(name: str) -> str:
    """清理不適合直接顯示在 Discord 的內容。"""

    if not isinstance(name, str):
        raise TypeError("網名必須是字串")

    name = name.strip()
    name = re.sub(r"\s+", " ", name)

    name = name.replace("@", "＠")
    name = name.replace("<", "＜")
    name = name.replace(">", "＞")
    name = name.replace("`", "ˋ")

    if not name or len(name) > 32:
        raise ValueError(
            "網名長度必須為 1～32 字元"
        )

    for term in FORBIDDEN_TERMS:
        if term.casefold() in name.casefold():
            raise ValueError(
                "網名包含不允許的內容"
            )

    return name


def generate_username(
    account_type: str,
    *,
    rng: random.Random | None = None,
    existing_names: Collection[str] = (),
    subject_alias: str | None = None,
    max_attempts: int = 100,
) -> str:
    """產生不與已提供名稱重複的虛構網名。

    subject_alias 只適用於粉絲類型，
    且必須是經上層確認可公開的角色暱稱。

    如果無法產生唯一名稱，會明確失敗。
    """

    if account_type not in ACCOUNT_TYPES:
        raise ValueError(
            f"不支援的網友類型：{account_type}"
        )

    if not 1 <= max_attempts <= 1000:
        raise ValueError(
            "max_attempts 必須為 1～1000"
        )

    generator = rng or random.SystemRandom()

    existing = {
        str(name).casefold()
        for name in existing_names
    }

    alias = None

    if subject_alias is not None:
        alias = normalize_username(
            subject_alias
        )

    fan_types = {
        "fan",
        "superfan",
        "solo_fan",
    }

    for attempt in range(max_attempts):
        prefix = generator.choice(
            PREFIXES[account_type]
        )

        if (
            account_type in fan_types
            and alias
        ):
            candidate = f"{prefix}{alias}"

        elif account_type == "fansite":
            candidate = (
                f"{prefix}_"
                f"{generator.choice(FANSITE_SUFFIXES)}"
            )

        else:
            candidate = (
                f"{prefix}"
                f"{generator.choice(SUFFIXES)}"
            )

        if attempt:
            candidate = (
                f"{candidate}{generator.randrange(10, 9999)}"
            )

        if len(candidate) > 32:
            candidate = candidate[:32]

        candidate = normalize_username(
            candidate
        )

        if candidate.casefold() not in existing:
            return candidate

    raise RuntimeError(
        "無法產生不重複的網名，"
        "請增加命名模板或調整條件"
    )
