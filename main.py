import os
import discord
from discord.ext import commands

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"✅ 機器人已上線：{bot.user}")

@bot.tree.command(name="測試", description="確認機器人是否正常運作")
async def test(interaction: discord.Interaction):
    await interaction.response.send_message(
        "🎉 機器人運作正常！",
        ephemeral=True
    )

if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("請先設定 DISCORD_TOKEN 環境變數")
    bot.run(token)
