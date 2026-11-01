"""總覽輸出字串的安全處理。"""
import discord

def escape(value, limit=130):
    s = discord.utils.escape_markdown(discord.utils.escape_mentions(str(value or "")))
    return s[:limit] or "（未填寫）"

