"""排行榜發獎開放前的唯讀盤點，避免把已封存誤認成已入帳。"""
from __future__ import annotations

from modules.leaderboard_catalog import BOARDS
from modules.leaderboard_reward_rules import calculate
from modules.leaderboard_payout_identity import CHARACTER_BOARDS
from modules.leaderboard_payout_policy import PAYOUT_ENABLED
from modules.leaderboard_reward_validation import validate_reward

# Mirrors the actual atomic payout handler rather than claiming all supported values pay.
ATOMIC_VALUE_FIELDS = frozenset({
    'heat', 'commercial', 'public_favor', 'pr', 'appearance', 'intellect',
    'acting', 'dance', 'sing', 'hosting', 'variety', 'talent',
    'free_points', 'skill_points', 'awareness',
})


def audit(guild_id=None):
    issues = []
    for board, title in BOARDS:
        if board == 'awards':
            issues.append({'board': board, 'title': title, 'reason': '第28榜未配置獎勵（獎項排行依活動逐步更新）'})
            continue
        try:
            reward = calculate(board, 1)
            validate_reward(reward)
            reasons = []
            if board not in CHARACTER_BOARDS:
                reasons.append('非角色型入帳適配器未完成')
            unmapped = sorted(set(reward.get('values', {})) - ATOMIC_VALUE_FIELDS)
            if unmapped:
                reasons.append('缺少欄位適配：' + '、'.join(unmapped))
            if reward.get('reward_version') != 'v11-approved':
                reasons.append('獎勵版本尚未核准')
            if reasons:
                issues.append({'board': board, 'title': title, 'reason': '；'.join(reasons)})
        except (ValueError, TypeError, KeyError) as exc:
            issues.append({'board': board, 'title': title, 'reason': str(exc)})
    source_issues = []
    if guild_id is not None:
        try:
            from modules.leaderboard_source_router import query
            for board, title in BOARDS:
                try:
                    _, status = query(guild_id, board, limit=5)
                    if status == 'unavailable':
                        source_issues.append({'board': board, 'title': title,
                                              'reason': '資料來源尚不可用；不會虛構排名'})
                except Exception as exc:
                    source_issues.append({'board': board, 'title': title,
                                          'reason': '資料來源查詢錯誤：' + type(exc).__name__})
        except ImportError as exc:
            source_issues.append({'board': '*', 'title': '資料來源整合', 'reason': str(exc)})
    return {'boards': len(BOARDS), 'blocked_boards': len(issues),
            'ready_to_enable': len(issues) == 0 and not source_issues and bool(PAYOUT_ENABLED),
            'global_payout_enabled': bool(PAYOUT_ENABLED), 'issues': issues,
            'source_issues': source_issues}


def summary(report, max_items=12):
    lines = ['🏆 **排行榜正式獎勵稽核**',
             f"總榜數：{report['boards']}｜待補或尚未核准：{report['blocked_boards']}",
             '正式派發開關：' + ('已開啟' if report['global_payout_enabled'] else '關閉（保護玩家資產）'),
             '14／30 天結算與快照不等於獎勵入帳。', '']
    for x in report['issues'][:max_items]:
        lines.append(f"• {x['title']}：{x['reason']}")
    if len(report['issues']) > max_items:
        lines.append(f"…另有 {len(report['issues']) - max_items} 榜待處理。")
    if report.get('source_issues'):
        lines.append(f"\n⚠️ 另有 {len(report['source_issues'])} 個排行榜資料來源待整合或報錯。")
        for entry in report['source_issues'][:3]:
            lines.append(f"• {entry['title']}：{entry['reason']}")
    if not report['issues'] and not report.get('source_issues'):
        lines.append('此檢查未發現規格阻擋；每期仍須核對真實收款對象與去重。')
    lines.append('⚠️ 本頁只讀，不會對金幣、屬性或粉絲執行轉帳。')
    return '\n'.join(lines)[:3900]
