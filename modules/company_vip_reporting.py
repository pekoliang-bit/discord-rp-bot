"""VIP 消費、退款及福利履約的唯讀報告。"""
from core.database import get_connection
from modules.company_vip import initialize_vip

def member_transactions(guild_id,business_id,character_id,limit=20):
    initialize_vip()
    with get_connection() as db:
        return [dict(r) for r in db.execute(
            "SELECT order_key,amount,kind,created_at FROM rp_company_vip_transactions WHERE guild_id=? AND business_id=? AND character_id=? ORDER BY id DESC LIMIT ?",
            (str(guild_id),str(business_id),int(character_id),max(1,min(int(limit),100)))).fetchall()]

def pending_benefits(guild_id,business_id,limit=50):
    initialize_vip()
    with get_connection() as db:
        return [dict(r) for r in db.execute(
            "SELECT id,character_id,product_reference,status,claimed_at FROM rp_company_vip_claims WHERE guild_id=? AND business_id=? AND status='reserved' ORDER BY id LIMIT ?",
            (str(guild_id),str(business_id),max(1,min(int(limit),100)))).fetchall()]
