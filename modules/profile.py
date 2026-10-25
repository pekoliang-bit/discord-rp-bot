
import discord
from discord import app_commands
from discord.ext import commands

from core.database import create_character, get_characters


class Profile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="建立角色",
        description="建立一份新的角色草稿"
    )
    @app_commands.describe(
        name="角色姓名",
        age="角色年齡",
        occupation="角色職業"
    )
    async def create_profile(
        self,
        interaction: discord.Interaction,
        name: str,
        age: app_commands.Range[int, 0, 200],
        occupation: str = "未設定"
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "此指令只能在伺服器內使用。",
                ephemeral=True
            )
            return

        name = name.strip()
        occupation = occupation.strip() or "未設定"

        if not name or len(name) > 100:
            await interaction.response.send_message(
                "角色姓名必須介於 1～100 字。",
                ephemeral=True
            )
            return

        character_id = create_character(
            guild_id=interaction.guild_id,
            owner_id=interaction.user.id,
            name=name,
            age=age,
            occupation=occupation
        )

        embed = discord.Embed(
            title="角色草稿建立成功",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="角色編號",
            value=str(character_id),
            inline=False
        )
        embed.add_field(name="姓名", value=name)
        embed.add_field(name="年齡", value=str(age))
        embed.add_field(
            name="職業",
            value=occupation,
            inline=False
        )
        embed.add_field(
            name="目前狀態",
            value="草稿，尚未完成正式審核",
            inline=False
        )
        embed.set_footer(
            text="測試版：尚未啟用雙重角色審核"
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

    @app_commands.command(
        name="角色",
        description="查看自己在此伺服器建立的角色"
    )
    async def view_profiles(
        self,
        interaction: discord.Interaction
    ):
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "此指令只能在伺服器內使用。",
                ephemeral=True
            )
            return

        characters = get_characters(
            guild_id=interaction.guild_id,
            owner_id=interaction.user.id
        )

        if not characters:
            await interaction.response.send_message(
                "你還沒有角色，先使用 /建立角色。",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="我的角色檔案",
            color=discord.Color.purple()
        )

        for character in characters[:20]:
            embed.add_field(
                name=(
                    f"#{character['id']} "
                    f"{character['name']}"
                ),
                value=(
                    f"年齡：{character['age']}\n"
                    f"職業：{character['occupation']}\n"
                    f"體力：{character['stamina']}/"
                    f"{character['max_stamina']}\n"
                    f"狀態：{character['status']}"
                ),
                inline=False
            )

        if len(characters) > 20:
            embed.set_footer(
                text="目前只顯示前 20 名角色"
            )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(Profile(bot))
