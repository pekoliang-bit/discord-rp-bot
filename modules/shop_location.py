"""官方商城 B01-B10 的位置與入口連結管理。"""
from __future__ import annotations
import re
from urllib.parse import urlparse
import discord
from core.database import transaction, get_connection

CODES = {f'B{i:02d}' for i in range(1,11)}

def init_db():
    with transaction() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS shop_locations (
          guild_id TEXT NOT NULL, store_code TEXT NOT NULL,
          placement TEXT NOT NULL DEFAULT 'mall' CHECK(placement IN ('mall','channel')),
          target_url TEXT NOT NULL DEFAULT '', updated_by TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(guild_id,store_code))''')

def get_location(guild_id,code):
    if code not in CODES: raise ValueError('商店代碼錯誤')
    init_db()
    with get_connection() as db:
        row=db.execute('SELECT placement,target_url FROM shop_locations WHERE guild_id=? AND store_code=?', (str(guild_id),code)).fetchone()
    return (row['placement'], row['target_url']) if row else ('mall','')

def validate_discord_link(guild_id, url):
    p=urlparse(url.strip())
    if p.scheme!='https' or p.hostname not in ('discord.com','www.discord.com','discordapp.com'):
        raise ValueError('目前其他頻道只接受 Discord 的 https 頻道／貼文連結')
    match=re.fullmatch(r'/channels/(\d+)/(\d+)(?:/(\d+))?/?',p.path)
    if not match or match.group(1)!=str(guild_id):
        raise ValueError('請使用此伺服器內有效的 Discord 頻道／貼文連結')
    return url.strip(), int(match.group(2))

def save_location(guild_id, code, placement, url, user_id):
    if code not in CODES: raise ValueError('商店編號必須為 B01～B10')
    if placement not in ('mall','channel'): raise ValueError('位置錯誤')
    if placement=='channel': validate_discord_link(guild_id,url)
    else: url=''
    init_db()
    with transaction() as db:
        db.execute('''INSERT INTO shop_locations(guild_id,store_code,placement,target_url,updated_by)
         VALUES(?,?,?,?,?) ON CONFLICT(guild_id,store_code) DO UPDATE SET
         placement=excluded.placement,target_url=excluded.target_url,updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP''',
         (str(guild_id),code,placement,url,str(user_id)))
    return f'✅ {code} 已改為'+ ('商城內' if placement=='mall' else f'其他頻道：{url}')

class ShopLocationModal(discord.ui.Modal, title='設定商店販售位置'):
    placement=discord.ui.TextInput(label='販售位置',placeholder='商城 或 頻道',max_length=6)
    url=discord.ui.TextInput(label='Discord 頻道／貼文連結',required=False,max_length=250)
    def __init__(self, code):
        super().__init__(); self.code=code
    async def on_submit(self, interaction:discord.Interaction):
        if not interaction.guild or interaction.user.id!=interaction.guild.owner_id:
            await interaction.response.send_message('只有群主能變更官方商店位置。',ephemeral=True);return
        try:
            kind={'商城':'mall','頻道':'channel','mall':'mall','channel':'channel'}[str(self.placement.value).strip().lower()]
            link=str(self.url.value or '').strip()
            if kind=='channel':
                link,cid=validate_discord_link(interaction.guild_id,link)
                dest=interaction.guild.get_channel_or_thread(cid)
                if dest is None:
                    try: dest=await interaction.guild.fetch_channel(cid)
                    except (discord.HTTPException,discord.Forbidden): dest=None
                if dest is None or getattr(dest,'guild',None)!=interaction.guild:
                    raise ValueError('機器人無法確認此頻道屬於本伺服器')
            msg=save_location(interaction.guild_id,self.code,kind,link,interaction.user.id)
        except KeyError: msg='⚠️ 販售位置請輸入「商城」或「頻道」。'
        except (ValueError,Exception) as exc: msg=f'⚠️ {exc}'
        await interaction.response.send_message(msg[:1900],ephemeral=True)

class LocationButton(discord.ui.Button):
    def __init__(self,code):
        super().__init__(label='📍 販售位置／連結',style=discord.ButtonStyle.secondary,row=3,disabled=not bool(code));self.code=code
    async def callback(self,interaction):
        if not interaction.guild or interaction.user.id!=interaction.guild.owner_id:
            await interaction.response.send_message('只有群主可設定官方商店位置。',ephemeral=True);return
        await interaction.response.send_modal(ShopLocationModal(self.code))

async def setup(bot):
    init_db()
