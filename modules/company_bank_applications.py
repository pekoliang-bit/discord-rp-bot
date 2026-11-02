"""Read-only company account application status for bank views."""
from __future__ import annotations
from core.database import get_connection


def company_application_status(guild_id, user_id, limit=8):
    from modules.application_extras import init_db
    init_db()
    with get_connection() as db:
        records=db.execute("""SELECT id,title,status,created_at,reviewed_at FROM rp_general_applications
            WHERE guild_id=? AND user_id=? AND kind='company_bank'
            ORDER BY id DESC LIMIT ?""",(str(guild_id),str(user_id),max(1,min(int(limit),20)))).fetchall()
    return [dict(row) for row in records]


def format_company_application_status(guild_id,user_id):
    labels={'pending':'⏳ 待審','approved':'✅ 通過','rejected':'❌ 駁回'}
    records=company_application_status(guild_id,user_id)
    if not records:
        return '🏢 **公司銀行申辦進度**\n尚無公司帳戶申請紀錄。請至 `/申請` 送件。'
    lines=['🏢 **公司銀行申辦進度**']
    for r in records:
        lines.append(f"#{r['id']}｜公司 {r['title']}｜{labels.get(r['status'],r['status'])}")
    lines.append('需要新增申請請使用 `/申請`。')
    return '\n'.join(lines)[:1800]
