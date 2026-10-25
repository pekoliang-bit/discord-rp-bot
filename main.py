
import discord
from discord import app_commands
from discord.ext import commands

from core.config import get_token, get_test_guild_id
from core.database import init_db

EXTENSIONS = [
    "modules.profile",
]


class RPBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=discord.Intents.default()
        )

    async def setup_hook(self):
        init_db()

        for extension in EXTENSIONS:
            await self.load_extension(extension)
            print(f"已載入：{extension}")

        test_guild_id = get_test_guild_id()

        if test_guild_id is not None:
            guild = discord.Object(id=test_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"測試伺服器同步 {len(synced)} 條指令")
        else:
            synced = await self.tree.sync()
            print(f"全域同步 {len(synced)} 條指令")

    async def on_ready(self):
        print(f"機器人已上線：{self.user}")


bot = RPBot()


@bot.tree.command(
    name="測試",
    description="確認機器人是否正常運作"
)
async def test_command(interaction: discord.Interaction):
    await interaction.response.send_message(
        "機器人運作正常！",
        ephemeral=True
    )


if __name__ == "__main__":
    bot.run(get_token())
