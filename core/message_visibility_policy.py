"""Discord RP 集中訊息可見性：保守公開白名單，敏感訊息不外洩。

只處理第一則斜線指令 response.send_message，不改按鈕、Modal、私訊、後續回覆。
Discord ephemeral 訊息不能事後轉成公開訊息，私密資訊仍須專用紀錄頻道。
"""
from __future__ import annotations

import functools
import logging
import sqlite3
from datetime import datetime, timezone

import discord
from core.database import get_connection

LOG = logging.getLogger(__name__)
MODES = {"public": "公開優先", "private": "私人優先", "original": "沿用原設定"}
# 只有已確認為一般資訊的頂層指令，才可以全域改寫首次回覆。
# 不能放入：查看、銀行、申請、審核、官方、登記、網路、惡意、資產等。
PUBLIC_INITIAL_COMMANDS = frozenset({"商城", "排行", "總覽", "群主指引", "系統狀態"})
# 內容與 UI 不可含個人資訊或管理操作；即使被誤用白名單也要拒絕公開。
SENSITIVE_WORDS = (
    "帳戶", "存款", "提款", "轉帳", "密碼", "驗證碼", "舉報", "申訴",
    "審核", "申請編號", "身分證字號", "私人資料", "私人資產", "黑卡",
    "信用卡", "貸款", "收款帳號", "個人資料", "角色專屬", "秘密", "機密",
)
_PATCHED = False


def init_tables():
    with get_connection() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS discord_message_visibility_settings (
            guild_id TEXT PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'original',
            updated_by TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK(mode IN ('public','private','original'))
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS discord_message_visibility_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id TEXT NOT NULL,
            user_id TEXT NOT NULL, command_name TEXT NOT NULL, channel_id TEXT NOT NULL,
            action TEXT NOT NULL, message_id TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        db.execute("""CREATE INDEX IF NOT EXISTS idx_visibility_audit_guild
                      ON discord_message_visibility_audit(guild_id, created_at)""")


def get_mode(guild_id):
    if not guild_id:
        return 'original'
    try:
        with get_connection() as db:
            row=db.execute("SELECT mode FROM discord_message_visibility_settings WHERE guild_id=?", (str(guild_id),)).fetchone()
            return str(row['mode']) if row and row['mode'] in MODES else 'original'
    except sqlite3.Error:
        LOG.exception('讀取訊息可見性設定失敗：採用原設定')
        return 'original'


def set_mode(guild_id, user_id, mode):
    if mode not in MODES:
        raise ValueError('無效的訊息可見性模式')
    with get_connection() as db:
        db.execute("""INSERT INTO discord_message_visibility_settings(guild_id,mode,updated_by)
                      VALUES (?,?,?) ON CONFLICT(guild_id) DO UPDATE SET mode=excluded.mode,
                      updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""",
                   (str(guild_id), mode, str(user_id)))


def get_count(guild_id):
    with get_connection() as db:
        return db.execute("SELECT COUNT(*) FROM discord_message_visibility_audit WHERE guild_id=?",
                          (str(guild_id),)).fetchone()[0]


def _safe_initial_public(interaction, content, kwargs):
    if not getattr(interaction, 'guild_id', None):
        return False
    # Only the first slash-command response, never modal/buttons, even if command is cached.
    if getattr(interaction, 'type', None) != discord.InteractionType.application_command:
        return False
    command=getattr(interaction, 'command', None)
    if getattr(command, 'name', None) not in PUBLIC_INITIAL_COMMANDS:
        return False
    # Embeds/views/attachments may contain sensitive data and must not be force-published.
    if any(kwargs.get(key) is not None for key in ('embed','embeds','view','file','files','poll','stickers')):
        return False
    if not isinstance(content,str) or not content.strip():
        return False
    if any(word in content for word in SENSITIVE_WORDS):
        return False
    return True


def install():
    global _PATCHED
    if _PATCHED or getattr(discord.InteractionResponse, '_rp_visibility_installed', False):
        return
    original=discord.InteractionResponse.send_message

    @functools.wraps(original)
    async def guarded_send(self, content=None, *args, **kwargs):
        interaction=getattr(self, '_parent', None)
        if interaction is not None and kwargs.get('ephemeral') is True:
            try:
                mode=get_mode(getattr(interaction,'guild_id',None))
                if mode == 'public' and _safe_initial_public(interaction,content,kwargs):
                    kwargs['ephemeral']=False
                    response=await original(self,content,*args,**kwargs)
                    # Metadata only; never store raw private content.
                    try:
                        with get_connection() as db:
                            db.execute("""INSERT INTO discord_message_visibility_audit
                              (guild_id,user_id,command_name,channel_id,action)
                              VALUES (?,?,?,?,?)""", (
                                  str(interaction.guild_id),str(interaction.user.id),
                                  str(interaction.command.name), str(interaction.channel_id), 'public_initial'))
                    except Exception:
                        LOG.exception('記錄公開指令元資料失敗（訊息已送出）')
                    return response
            except Exception:
                LOG.exception('訊息可見性防護失敗：沿用原本私密設定')
        return await original(self,content,*args,**kwargs)

    discord.InteractionResponse.send_message=guarded_send
    discord.InteractionResponse._rp_visibility_installed=True
    _PATCHED=True
