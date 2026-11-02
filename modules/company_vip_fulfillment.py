"""VIP 福利交付狀態回寫。僅在外部商品發貨成功之後標記完成。"""
import time
from core.database import get_connection
from modules.company_vip import initialize_vip

def complete_claim(guild_id,business_id,claim_id,product_reference):
    """商品系統確認已發貨後，使用相同商品識別資訊呼叫。冪等。"""
    initialize_vip()
    with get_connection() as db:
        claim=db.execute(
          "SELECT status,product_reference FROM rp_company_vip_claims WHERE id=? AND guild_id=? AND business_id=?",
          (int(claim_id),str(guild_id),str(business_id))).fetchone()
        if not claim:
            raise ValueError('找不到對應 VIP 福利領取紀錄')
        if str(claim['product_reference']) != str(product_reference):
            raise ValueError('商品識別不符合已保留的福利')
        if claim['status']=='completed':
            return False
        if claim['status']!='reserved':
            raise ValueError('福利狀態不允許完成')
        db.execute(
          "UPDATE rp_company_vip_claims SET status='completed',completed_at=? WHERE id=? AND status='reserved'",
          (int(time.time()),int(claim_id)))
        return True
