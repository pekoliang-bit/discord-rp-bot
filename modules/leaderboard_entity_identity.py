
"""排行榜獲獎單位身分識別。

此模組只負責建立與解析識別鍵。
尚未自動改寫舊快照。
"""

VALID_TYPES = {
    "character",
    "company",
    "group",
    "shop",
    "faction",
    "cp",
    "social_account",
}

PREFIX = "lbentity"


def make_identity(entity_type, entity_id):
    """建立有類型的唯一識別鍵。"""
    entity_type = str(entity_type).strip()
    entity_id = str(entity_id).strip()

    if entity_type not in VALID_TYPES:
        raise ValueError("不支援的獲獎單位類型")

    if not entity_id:
        raise ValueError("獲獎單位 ID 不可為空")

    # 使用長度前綴，避免特殊符號造成解析混淆。
    return (
        f"{PREFIX}:{entity_type}:"
        f"{len(entity_id)}:{entity_id}"
    )


def parse_identity(value):
    """解析識別鍵，舊式裸 ID 不會被猜測類型。"""
    value = str(value)

    parts = value.split(":", 3)

    if len(parts) != 4:
        return None

    prefix, entity_type, length_text, entity_id = parts

    if prefix != PREFIX:
        return None

    if entity_type not in VALID_TYPES:
        return None

    try:
        expected_length = int(length_text)
    except ValueError:
        return None

    if expected_length < 1:
        return None

    if len(entity_id) != expected_length:
        return None

    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
    }


def same_identity(first, second):
    """只有完整類型與 ID 相同才算同一單位。"""
    a = parse_identity(first)
    b = parse_identity(second)

    return a is not None and a == b
