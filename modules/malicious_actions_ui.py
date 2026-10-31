
"""/惡意 V1：角色對角色行動與群主機器人帖子綁定。

/惡意只在已綁定的角色專屬機器人帖子可用。
/惡意帖子設定由群主在該角色既有論壇內任一帖子操作。

真實扣款沿用 core.database 雙向分錄，與行動寫入同筆 SQLite 交易。
傳聞建立需另外由正式傳聞審核流程核准；本模組不擅自公開造謠。
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from core.database import get_or_create_account, account_balance
from core.malicious_actions import MaliciousRequest, resolve_action, LEVEL_COST
from core.malicious_forum_permissions import (
    authorize_malicious, register_bot_thread,
)

ACTION_CHOICES = [
    app_commands.Choice(name='造謠／抹黑', value='rumor'),
    app_commands.Choice(name='商業打壓', value='business'),
    app_commands.Choice(name='工作陷害', value='work'),
    app_commands.Choice(name='人際挑撥', value='relationship'),
    app_commands.Choice(name='情報調查', value='intelligence'),
    app_commands.Choice(name='特殊惡意行動（需官方核准）', value='special'),
]
LEVEL_CHOICES = [
    app_commands.Choice(name=f'{key}：{cost:,} 金幣', value=key)
    for key, cost in LEVEL_COST.items()
]


def debit_character_wallet(db, guild_id: str, actor_character_id: int,
                           amount: int, unique_key: str):
    """與 resolve_action 共用交易；金幣轉入系統行動費用帳戶。"""
    if amount <= 0:
        raise ValueError('金額必須大於零')
    before = db.execute('SELECT id FROM ledger_transactions WHERE guild_id=? AND unique_key=?',
                        (guild_id, unique_key)).fetchone()
    if before:
        raise ValueError('同一筆惡意行動不可重複扣款')
    payer = get_or_create_account(db, guild_id, 'character', actor_character_id, 'wallet')
    receiver = get_or_create_account(db, guild_id, 'system', 'malicious_fees', 'wallet')
    if payer == receiver:
        raise RuntimeError('付款帳戶與收款帳戶不能相同')
    if account_balance(db, guild_id, payer) < amount:
        raise ValueError('角色錢包金幣不足')
    cursor = db.execute('''INSERT INTO ledger_transactions(guild_id,unique_key,description)
                           VALUES (?,?,?)''', (guild_id, unique_key, '惡意行動費用'))
    tid = cursor.lastrowid
    db.executemany('''INSERT INTO ledger_entries(guild_id,transaction_id,account_id,amount)
                      VALUES (?,?,?,?)''',
                   [(guild_id, tid, payer, -amount), (guild_id, tid, receiver, amount)])


def create_rumor_if_ready(guild_id: str, action_id: int) -> tuple[int | None, str]:
    """基於正式啟用的官方規則，為成功惡意行動建立傳聞。

    沒有核准的分級、複雜度或獎懲規則時不猜測數值；
    保留私人行動紀錄，稍後可由正式流程補建。
    """
    from core.database import transaction
    from core.malicious_actions import TYPES
    from core.malicious_rumor_bridge import (
        RumorCreationSettings, create_rumor_for_action,
    )
    from core.rumor_rule_registry import get_active_rules
    from core.rumor_tier_policy import resolve_tier_in_transaction
    from core.rumor_complexity_policy import resolve_complexity_in_transaction

    with transaction() as db:
        row = db.execute(
            "SELECT * FROM rp_malicious_actions WHERE guild_id=? AND id=?",
            (str(guild_id), action_id),
        ).fetchone()
        if row is None:
            raise LookupError("惡意行動不存在")
        if not row["succeeded"]:
            return None, "行動未成功，沒有對目標建立傳聞"
        if row["rumor_id"] is not None:
            return int(row["rumor_id"]), "已關聯傳聞案件"
        source = "造謠" if row["action_type"] == "rumor" else "玩家"
        category = TYPES[row["action_type"]]
        tier = resolve_tier_in_transaction(db, guild_id, category, source)
        complexity = resolve_complexity_in_transaction(db, guild_id, category, source)

    rules = get_active_rules(guild_id)
    settings = RumorCreationSettings(
        business_id=f"malicious:{action_id}",
        tier=tier,
        complexity=complexity.complexity,
        complexity_version=complexity.version,
        rules=rules,
    )
    rumor_id = create_rumor_for_action(
        guild_id, action_id, settings=settings,
        case_authorized=True, publicly_visible=True,
    )
    return rumor_id, "已自動建立傳聞案件" if rumor_id else "無需建立傳聞"


class MaliciousActionsUI(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name='惡意帖子設定', description='群主將目前論壇帖子登記為角色的機器人使用帖子')
    @app_commands.guild_only()
    @app_commands.describe(角色編號='該論壇所屬角色的角色 ID')
    async def configure_thread(self, interaction: discord.Interaction, 角色編號: int):
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or interaction.user.id != guild.owner_id:
            await interaction.response.send_message('⛔ 只有群主能登記機器人使用帖子。', ephemeral=True)
            return
        if not isinstance(channel, discord.Thread) or not isinstance(channel.parent, discord.ForumChannel):
            await interaction.response.send_message('⛔ 必須在該角色的論壇帖子內設定。', ephemeral=True)
            return
        try:
            register_bot_thread(guild.id, 角色編號, channel.parent_id, channel.id,
                                setter_id=interaction.user.id, owner_authorized=True)
        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.response.send_message(f'⚠️ 設定失敗：{exc}', ephemeral=True)
            return
        await interaction.response.send_message(
            f'✅ 已將 <#{channel.id}> 綁定為角色 #{角色編號} 的「機器人使用」帖子。\n'
            '提醒：此綁定只限 /惡意；其他機器人指令的權限另由各系統判斷。', ephemeral=True)

    @app_commands.command(name='惡意', description='在自己角色的機器人使用帖子發動角色對角色行動')
    @app_commands.guild_only()
    @app_commands.describe(目標角色編號='要針對的已啟用角色 ID',
                           行動='惡意行動類型', 強度='金幣投入等級',
                           標題='傳聞或行動標題（1～80字）',
                           內容='RP 劇情敘述（1～500字）', 匿名='是否隱藏發起角色')
    @app_commands.choices(行動=ACTION_CHOICES, 強度=LEVEL_CHOICES)
    async def malicious(self, interaction: discord.Interaction,
                        目標角色編號: int, 行動: app_commands.Choice[str],
                        強度: app_commands.Choice[str], 標題: str, 內容: str,
                        匿名: bool = True):
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.Thread) or not isinstance(channel.parent, discord.ForumChannel):
            await interaction.response.send_message(
                '⛔ /惡意只能在自己角色論壇的「機器人使用」帖子操作。', ephemeral=True)
            return
        is_owner = interaction.user.id == guild.owner_id
        try:
            authorization = authorize_malicious(
                guild.id, interaction.user.id, channel.id, channel.parent_id,
                owner_override=is_owner,
            )
        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.response.send_message(f'⛔ {exc}', ephemeral=True)
            return
        if 行動.value == 'special':
            await interaction.response.send_message(
                '⚠️ 特殊惡意事件需先經官方審核，目前不直接扣款或結算。', ephemeral=True)
            return
        request = MaliciousRequest(
            guild_id=authorization.guild_id,
            actor_user_id=authorization.actor_user_id,
            actor_character_id=authorization.character_id,
            target_character_id=目標角色編號,
            action_type=行動.value,
            level=強度.value,
            anonymous=匿名,
            title=標題,
            description=內容,
        )
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await __import__('asyncio').to_thread(
                resolve_action, request,
                debit=debit_character_wallet,
                forum_authorized=True,
                trusted_stats=True,
                owner_override=is_owner,
            )
        except (ValueError, PermissionError, LookupError) as exc:
            await interaction.followup.send(f'⚠️ 行動未成立，未扣款：{exc}', ephemeral=True)
            return
        except Exception:
            import logging
            logging.getLogger(__name__).exception('惡意行動結算失敗')
            await interaction.followup.send('⚠️ 系統結算失敗；請聯絡管理員確認帳本。', ephemeral=True)
            return
        rumor_note = ""
        if result.succeeded:
            try:
                rumor_id, rumor_status = await __import__('asyncio').to_thread(
                    create_rumor_if_ready, authorization.guild_id, result.action_id,
                )
                rumor_note = (
                    f"\n傳聞：#{rumor_id}（{rumor_status}）" if rumor_id
                    else f"\n傳聞：{rumor_status}"
                )
            except (LookupError, PermissionError, ValueError) as exc:
                rumor_note = f"\n傳聞：等待官方規則／授權確認（{exc}）"
            except Exception:
                import logging
                logging.getLogger(__name__).exception('惡意傳聞建案失敗')
                rumor_note = "\n傳聞：尚未建立，請管理員檢查傳聞設定；行動與扣款不重複執行"
        outcome = '行動成功' if result.succeeded else '行動失敗'
        status = '可能留下線索' if result.exposed else '尚未暴露'
        await interaction.followup.send(
            f'🎭 惡意行動 #{result.action_id}：**{outcome}**\n'
            f'投入：{result.cost:,} 金幣｜判定成功率：{result.success_chance}%\n'
            f'來源：{status}\n'
            f'{rumor_note}\n傳聞指控不等於已證實事實。', ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(MaliciousActionsUI(bot))
