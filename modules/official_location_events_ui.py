"""官方地點隨機劇情事件的 Discord 選擇介面，不註冊額外 slash 指令。"""
from __future__ import annotations

import asyncio
import logging
import discord

from core import official_location_events_v1 as events

LOG=logging.getLogger(__name__)


class OfficialEventChoiceView(discord.ui.View):
    def __init__(self, guild_id, user_id, character_id, event):
        super().__init__(timeout=300)
        self.guild_id=str(guild_id)
        self.user_id=int(user_id)
        self.character_id=int(character_id)
        self.event=event
        for choice in event['choices']:
            button=discord.ui.Button(label=str(choice['label'])[:80],style=discord.ButtonStyle.secondary)
            key=str(choice['key'])
            async def callback(i: discord.Interaction, selected_key=key):
                await i.response.defer(ephemeral=True)
                try:
                    outcome=await asyncio.to_thread(events.choose,self.guild_id,
                        self.event['request_key'],self.character_id,i.user.id,selected_key)
                except (ValueError,PermissionError) as exc:
                    await i.followup.send(str(exc),ephemeral=True)
                    return
                except Exception:
                    LOG.exception('官方場所事件選擇失敗')
                    await i.followup.send('事件尚未完成，請稍後再試。',ephemeral=True)
                    return
                self.stop()
                for child in self.children:child.disabled=True
                await i.edit_original_response(content=f'🎲 {self.event["title"]}\n{outcome["response"]}',view=self)
            button.callback=callback
            self.add_item(button)

    async def interaction_check(self, i):
        if i.guild_id is None or str(i.guild_id)!=self.guild_id or i.user.id!=self.user_id:
            await i.response.send_message('此事件只可由原執行者操作。',ephemeral=True)
            return False
        return True


def describe(event):
    return f'🎲 **{event["title"]}**\n{event["description"]}\n請選擇事件回應（純劇情，不更動金幣或數值）。'
