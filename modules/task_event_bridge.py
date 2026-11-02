"""供各斜線指令成功顯示教學頁後呼叫；不能僅靠指令名稱推定成功。"""
from __future__ import annotations
from modules.task_newbie import REQUIRED_COMMANDS, record_success, progress, can_use_training_channel

def training_allowed(guild_id, character_id, user_id, channel_id, command):
    return can_use_training_channel(guild_id,character_id,user_id,channel_id,command)

def completed_tutorial_view(guild_id, character_id, user_id, channel_id, command):
    if command not in REQUIRED_COMMANDS: return False
    return record_success(guild_id,character_id,user_id,channel_id,command)

def tutorial_status(guild_id,character_id):
    return progress(guild_id,character_id)
