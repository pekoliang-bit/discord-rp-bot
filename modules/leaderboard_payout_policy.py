"""排行榜實際入帳安全開關。"""

from modules.leaderboard_reward_validation import validate_reward
from modules.leaderboard_payout_identity import validate_identity

PAYOUT_ENABLED = False


def inspect_reward(board, entity_key, reward):
    issues = []

    try:
        validate_identity(board, entity_key)
    except (ValueError, TypeError) as exc:
        issues.append(str(exc))

    try:
        validate_reward(reward)
    except (ValueError, TypeError, KeyError) as exc:
        issues.append(str(exc))

    if not isinstance(reward, dict):
        return {
            "allowed": False,
            "issues": issues,
        }

    if reward.get("board") != board:
        issues.append("獎勵所屬排行榜不一致")

    if reward.get("reward_version") == "v11-provisional":
        issues.append("暫定獎勵尚未正式核准")

    # Data-schema validity alone does not imply that an accounting adapter exists.
    # Keep these checks synchronized with the only currently implemented payout path.
    from modules.leaderboard_payout_identity import CHARACTER_BOARDS
    if board not in CHARACTER_BOARDS:
        issues.append("非角色型排行榜尚無正式派發適配器")
    # These names are the fields currently supported by leaderboard_payout_service.
    mapped_values = frozenset((
        'heat', 'commercial', 'public_favor', 'pr', 'appearance', 'intellect',
        'acting', 'dance', 'sing', 'hosting', 'variety', 'talent',
        'free_points', 'skill_points', 'awareness',
    ))
    values = reward.get("values", {})
    if isinstance(values, dict):
        unmapped = set(values) - mapped_values
        if unmapped:
            issues.append("獎勵缺少正式入帳適配器：" + ", ".join(sorted(map(str, unmapped))))

    if not PAYOUT_ENABLED:
        issues.append("實際入帳功能尚未啟用")

    return {
        "allowed": not issues,
        "issues": issues,
    }


def require_payout_permission(board, entity_key, reward):
    result = inspect_reward(board, entity_key, reward)
    if not result["allowed"]:
        raise PermissionError(
            "；".join(result["issues"])
        )
    return True
