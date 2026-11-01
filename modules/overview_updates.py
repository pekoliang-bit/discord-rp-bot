"""只讀通知更新彙總，使用既有 notification_update_items，查詢時不建表、不發布通知。"""
import sqlite3
from core.database import get_connection
from modules.overview_helpers import escape

def recent_updates(guild_id, limit=12):
    try:
        with get_connection() as db:
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notification_update_items'").fetchone()
            if not exists:
                return "尚未建立更新公告資料，請從 `/通知` 查看設定。"
            rows=db.execute("SELECT category,title,action,source_url,created_at FROM notification_update_items WHERE guild_id=? ORDER BY id DESC LIMIT ?",(str(guild_id),min(20,max(1,int(limit))))).fetchall()
    except (sqlite3.Error, KeyError, TypeError):
        return "更新資料暫時無法讀取；請使用 `/通知`。"
    if not rows:
        return "目前沒有已記錄的內容更新。可使用 `/通知` 查詢公告。"
    lines=[]
    for r in rows:
        r=dict(r)
        title=escape(r.get('title'),72)
        category=escape(r.get('category'),22)
        action=escape(r.get('action'),12)
        url=r.get('source_url')
        link=f" [查看](<{url}>)" if isinstance(url,str) and url.startswith('https://discord.com/channels/') else ""
        lines.append(f"• **{category}**｜{action} {title}{link}")
    return "\n".join(lines)[:3500]
