
"""排行榜點數、稱號及結算檢查的整合入口。

此模組不註冊 Discord 指令。
不自動發放金幣。
"""

from modules.leaderboard_points_catalog import (
    available_targets,
)
from modules.leaderboard_points_storage import (
    get_balance,
    get_history,
)
from modules.leaderboard_points_preview import preview
from modules.leaderboard_points_service import allocate
from modules.leaderboard_title_service import (
    get_collection,
    stage_titles,
)
from modules.leaderboard_settlement_guard import (
    inspect_period,
)


def character_points_panel(guild_id, character_id):
    """供 /查看 面板取得點數資料。"""
    return {
        "balances": get_balance(
            guild_id, character_id
        ),
        "targets": {
            "free": available_targets("free"),
            "skill": available_targets("skill"),
        },
        "history": get_history(
            guild_id, character_id, 20
        ),
    }


def preview_allocation(
    guild_id,
    character_id,
    user_id,
    pool,
    category,
    target,
    amount,
):
    """第一階段：預覽，尚未扣點。"""
    return preview(
        guild_id,
        character_id,
        user_id,
        pool,
        category,
        target,
        amount,
    )


def confirm_allocation(
    guild_id,
    character_id,
    user_id,
    pool,
    category,
    target,
    amount,
    request_key,
):
    """第二階段：確認後正式提交。"""
    return allocate(
        guild_id,
        character_id,
        user_id,
        pool,
        category,
        target,
        amount,
        request_key,
    )


def character_title_panel(
    guild_id,
    entity_key,
    current_index,
):
    return get_collection(
        guild_id,
        entity_key,
        current_index,
    )


def process_settlement_titles(guild_id, index):
    """已封存30天快照才允許建立稱號。"""
    period = inspect_period(
        guild_id,
        "30d",
        index,
    )

    if not period["snapshotted"]:
        raise ValueError("尚未完成30天結算快照")

    return stage_titles(guild_id, index)


def settlement_status(guild_id, cycle, index):
    return inspect_period(
        guild_id,
        cycle,
        index,
    )
