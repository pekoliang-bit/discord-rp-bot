"""第四階段：資產與金融清算前唯讀盤點。

只做 scoped 查詢，不建立、刪除、分配資產，也不替任何人核准退場。
呼叫方必須在 UI 外層驗證角色身分；本人可透過 report_for_character 驗證。
"""
from __future__ import annotations

from core.database import transaction
from core import large_assets as assets
from core import asset_completions as extras


def _table_exists(db, table: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _count(db, query: str, params: tuple) -> int:
    return int(db.execute(query, params).fetchone()[0] or 0)


def report_in_transaction(db, guild, holder_type, holder_id):
    """產生清算阻礙清單。只接受 character / organization 兩種主體。"""
    if holder_type not in ('character', 'organization'):
        raise assets.AssetError('不支援的持有人類型')
    key, hid = assets.gid(guild), str(holder_id)
    extras.initialize(db)
    shares = db.execute('''SELECT s.asset_id,s.basis_points,i.label
        FROM rp_asset_shares s JOIN rp_asset_items i
          ON i.guild_id=s.guild_id AND i.id=s.asset_id
        WHERE s.guild_id=? AND s.holder_type=? AND s.holder_id=?
        ORDER BY s.asset_id''', (key, holder_type, hid)).fetchall()
    asset_ids = [int(x['asset_id']) for x in shares]
    active_orders = _count(db, '''SELECT COUNT(*) FROM rp_asset_orders
        WHERE guild_id=? AND seller_type=? AND seller_id=? AND status='open' ''',
        (key, holder_type, hid))
    active_landlord_leases = _count(db, '''SELECT COUNT(*) FROM rp_asset_leases
        WHERE guild_id=? AND landlord_type=? AND landlord_id=? AND status='active' ''',
        (key, holder_type, hid))
    active_tenant_leases = 0
    bank_loans = 0
    if holder_type == 'character':
        active_tenant_leases = _count(db, '''SELECT COUNT(*) FROM rp_asset_leases
            WHERE guild_id=? AND tenant_id=? AND status='active' ''', (key,int(hid)))
        if _table_exists(db, 'bank_loans'):
            bank_loans = _count(db, '''SELECT COUNT(*) FROM bank_loans
                WHERE guild_id=? AND character_id=? AND status IN ('pending','active','overdue')''',
                (key,int(hid)))
    pending_share_invites = 0
    if _table_exists(db, 'rp_asset_share_requests'):
        pending_share_invites = _count(db, '''SELECT COUNT(*) FROM rp_asset_share_requests
          WHERE guild_id=? AND status='pending' AND
          ((from_type=? AND from_id=?) OR (to_character=? AND ?='character'))''',
          (key,holder_type,hid,int(hid) if hid.isdecimal() else -1,holder_type))
    liens = 0
    unpaid_fees = 0
    for aid in asset_ids:
        liens += _count(db, '''SELECT COUNT(*) FROM rp_asset_lien WHERE guild_id=?
            AND asset_id=? AND status='active' AND outstanding_amount>0''', (key,aid))
        unpaid_fees += _count(db, '''SELECT COALESCE(SUM(due_amount-paid_amount),0)
            FROM rp_asset_dues WHERE guild_id=? AND asset_id=? AND due_amount>paid_amount''', (key,aid))
    result = {
        'holder_type': holder_type,
        'holder_id': hid,
        'shares': [dict(r) for r in shares],
        'active_orders': active_orders,
        'active_landlord_leases': active_landlord_leases,
        'active_tenant_leases': active_tenant_leases,
        'bank_loans': bank_loans,
        'pending_share_invites': pending_share_invites,
        'active_liens': liens,
        'unpaid_maintenance': unpaid_fees,
    }
    result['ready_for_asset_exit'] = not any((
        asset_ids, active_orders, active_landlord_leases,
        active_tenant_leases, bank_loans, pending_share_invites, liens, unpaid_fees,
    ))
    return result


def report_for_character(guild, character_id, user_id):
    """玩家只能查看自己的角色；不向其他人揭露銀行或資產資訊。"""
    with transaction() as db:
        assets.character(db, guild, character_id, user_id)
        return report_in_transaction(db,guild,'character',character_id)


def report_for_organization(guild, business_id, user_id):
    with transaction() as db:
        assets._company(db,guild,business_id,user_id)
        return report_in_transaction(db,guild,'organization',business_id)


def describe_report(report):
    title = '角色' if report['holder_type']=='character' else '公司'
    lines = [f'🏛️ **{title} #{report["holder_id"]}：資產／金融清算前盤點**',
             f'持有大型資產：{len(report["shares"])} 項',
             f'未結束掛單：{report["active_orders"]} 筆',
             f'房東有效租約：{report["active_landlord_leases"]} 筆',
             f'房客有效租約：{report["active_tenant_leases"]} 筆',
             f'銀行待審／未結清貸款：{report["bank_loans"]} 筆',
             f'待確認持分邀請：{report["pending_share_invites"]} 筆',
             f'有效抵押：{report["active_liens"]} 筆',
             f'未繳大型資產維護費：{report["unpaid_maintenance"]:,} 金幣']
    if report['shares']:
        lines.append('資產持分：'+ '、'.join(f'#{r["asset_id"]} {r["basis_points"]/100:.2f}%' for r in report['shares'][:10]))
    lines.append('✅ 大型資產與貸款盤點無阻礙' if report['ready_for_asset_exit'] else '⚠️ 尚有未結事項，禁止直接刪除角色或解散公司')
    lines.append('注意：本報告不代表公司、商品背包、職位、現金等其他系統已完成清算。')
    return '\n'.join(lines)
