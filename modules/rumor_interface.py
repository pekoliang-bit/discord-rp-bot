"""Discord /傳聞 玩家操作入口（整合測試版）。管理設定仍歸 /官方。

所有操作只在 Discord 伺服器執行；每次互動都重新檢查角色權限。
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from modules import pr_rumor, rumor_investigation, rumor_police, rumor_network, rumor_public_relations


def _integer(value, label):
    try:
        number = int(str(value).strip())
        if number <= 0:
            raise ValueError
        return number
    except ValueError as exc:
        raise ValueError(f"{label}必須是正整數。") from exc


class GenericRumorModal(discord.ui.Modal):
    def __init__(self, purpose, fields):
        super().__init__(title=purpose[:45], timeout=180)
        self.purpose = purpose
        self.fields = []
        for label, placeholder in fields:
            item = discord.ui.TextInput(label=label[:45], placeholder=placeholder[:100], max_length=500)
            self.fields.append(item)
            self.add_item(item)

    async def on_submit(self, interaction):
        try:
            values = [item.value.strip() for item in self.fields]
            gid, uid = interaction.guild_id, interaction.user.id
            purpose = self.purpose
            if purpose == "設定傳聞分類授權":
                cid = _integer(values[0], "角色編號")
                if values[2] not in ("開啟", "關閉"):
                    raise ValueError("狀態請填寫開啟或關閉。")
                pr_rumor.set_category_consent(gid, uid, cid, values[1], values[2] == "開啟")
                result = "分類授權已更新。"
            elif purpose == "登記證據":
                if values[4] not in ("真", "偽"):
                    raise ValueError("真偽必須填真或偽。")
                eid = rumor_investigation.register_evidence(
                    gid, uid, _integer(values[0], "傳聞編號"),
                    _integer(values[1], "角色編號"), values[2], values[3], authentic=values[4] == "真")
                result = f"證據 #{eid} 已登記。"
            elif purpose == "進行基礎調查":
                data = rumor_investigation.attempt_basic_investigation(
                    gid, uid, _integer(values[0], "傳聞編號"), _integer(values[1], "角色編號"))
                result = f"{'成功' if data['success'] else '未取得有效線索'}；調查進度 {data['progress']}/100。"
            elif purpose == "虛構警方報案":
                evidence = _integer(values[2], "證據編號") if values[2] else None
                data = rumor_police.report_rumor(
                    gid, uid, _integer(values[0], "傳聞編號"),
                    _integer(values[1], "角色編號"), evidence)
                result = f"已受理警方案件 #{data['police_case_id']}；預計結案 <t:{data['due_at']}:F>。"
            elif purpose == "向警方補交證據":
                added = rumor_police.submit_evidence(
                    gid, uid, _integer(values[0], "警方案件編號"),
                    _integer(values[1], "角色編號"), _integer(values[2], "證據編號"))
                result = "證據已提交。" if added else "這份證據已經提交過。"
            elif purpose == "網路公開曝光":
                eid = rumor_network.publish_exposure(
                    gid, uid, _integer(values[0], "傳聞編號"),
                    _integer(values[1], "角色編號"), values[2], values[3])
                result = f"網路曝光紀錄 #{eid} 已建立。"
            elif purpose == "提供網友線索":
                eid = rumor_network.submit_crowd_clue(
                    gid, uid, _integer(values[0], "傳聞編號"),
                    _integer(values[1], "角色編號"), values[2], values[3])
                result = f"線索 #{eid} 已提交，等待查證。"
            elif purpose == "公關策略":
                data = rumor_public_relations.record_free_pr_action(
                    gid, uid, _integer(values[0], "傳聞編號"),
                    _integer(values[1], "角色編號"), values[2])
                result = f"策略已記錄，本次效果 +{data['points']}，效益 {data['multiplier_percent']}%。"
            elif purpose == "查詢警方案件":
                data = rumor_police.police_case_status(
                    gid, uid, _integer(values[0], "警方案件編號"), _integer(values[1], "角色編號"))
                result = f"案件 #{data['id']}：{data['status']}，已提交證據 {data['evidence_count']} 份。"
            elif purpose == "查看傳聞結果":
                data = rumor_public_relations.preview_settlement(gid, _integer(values[0], "傳聞編號"))
                result = f"案件 #{data['rumor_id']}：{data['case_status']}；暫定 {data['rank']}（{data['label']}），公關分數 {data['score']}。"
            else:
                raise ValueError("未知操作。")
        except (ValueError, PermissionError, TypeError) as exc:
            result = f"❌ {exc}"
        await interaction.response.send_message(result[:1900], ephemeral=True)


FORM_DEFINITIONS = {
    "category": ("設定傳聞分類授權", [("角色編號", "例如 15"), ("分類", "戀情／商業／工作／人際／私生活／其他"), ("狀態", "開啟／關閉")]),
    "evidence": ("登記證據", [("傳聞編號", "例如 8"), ("持有角色編號", "自己已啟用的角色"), ("證據類型", "目擊／對話／照片／影音／文件／交易／其他"), ("可信度", "低／中／高／決定性"), ("真偽", "真／偽")]),
    "investigate": ("進行基礎調查", [("傳聞編號", "例如 8"), ("調查角色編號", "自己的角色")]),
    "report": ("虛構警方報案", [("傳聞編號", "例如 8"), ("報案角色編號", "自己的角色"), ("證據編號（可留空）", "選填")]),
    "submit": ("向警方補交證據", [("警方案件編號", "例如 2"), ("持有角色編號", "自己的角色"), ("證據編號", "例如 6")]),
    "expose": ("網路公開曝光", [("傳聞編號", "例如 8"), ("角色編號", "自己的角色"), ("曝光形式", "公開爆料／媒體報導"), ("標題", "80 字內")]),
    "crowd": ("提供網友線索", [("傳聞編號", "例如 8"), ("角色編號", "自己的角色"), ("線索類型", "目擊線索／內容比對／知情者爆料／意外牽連"), ("內容", "5 至 500 字")]),
    "pr": ("公關策略", [("傳聞編號", "例如 8"), ("角色編號", "自己的角色"), ("策略", "公開澄清／提出證據／接受訪問／公開道歉／法律聲明／沉默觀察")]),
    "police_status": ("查詢警方案件", [("警方案件編號", "例如 2"), ("報案角色編號", "自己的角色")]),
    "result": ("查看傳聞結果", [("傳聞編號", "例如 8")]),
}


class RumorActionSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="建立傳聞", value="create"),
            discord.SelectOption(label="查看案件清單", value="records"),
            discord.SelectOption(label="整體傳聞授權", value="consent"),
            discord.SelectOption(label="設定分類授權", value="category"),
            discord.SelectOption(label="登記證據", value="evidence"),
            discord.SelectOption(label="基礎調查", value="investigate"),
            discord.SelectOption(label="報警", value="report"),
            discord.SelectOption(label="警方補交證據", value="submit"),
            discord.SelectOption(label="網路曝光", value="expose"),
            discord.SelectOption(label="網友線索", value="crowd"),
            discord.SelectOption(label="公關策略", value="pr"),
            discord.SelectOption(label="警方案件查詢", value="police_status"),
            discord.SelectOption(label="公關結果預覽", value="result"),
        ]
        super().__init__(placeholder="選擇傳聞操作", options=options, min_values=1, max_values=1)

    async def callback(self, interaction):
        key = self.values[0]
        if key == "create":
            await interaction.response.send_modal(pr_rumor.RumorModal())
            return
        if key == "consent":
            await interaction.response.send_modal(pr_rumor.ConsentModal())
            return
        if key == "records":
            try:
                rows = pr_rumor.list_rumors(interaction.guild_id, interaction.user.id)
                msg = "\n".join(
                    f"#{r['id']}｜角色#{r['character_id']}｜{r['title']}｜{r['status']}"
                    for r in rows
                ) or "目前沒有可查看的案件。"
            except (ValueError, LookupError, PermissionError) as exc:
                msg = f"❌ {exc}"
            await interaction.response.send_message(msg[:1900], ephemeral=True)
            return
        title, fields = FORM_DEFINITIONS[key]
        await interaction.response.send_modal(GenericRumorModal(title, fields))


class RumorPlayerPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)
        self.add_item(RumorActionSelect())


class RumorInterface(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="傳聞", description="傳聞、調查、網路曝光、報警與公關操作")
    @app_commands.guild_only()
    async def rumor(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "📰 傳聞中心｜選擇你要執行的操作。\n目前是整合測試版本；管理功能統一放在 /官方。",
            view=RumorPlayerPanel(), ephemeral=True)


async def setup(bot):
    pr_rumor.initialize()
    rumor_police.initialize()
    rumor_network.initialize()
    rumor_public_relations.initialize()
    await bot.add_cog(RumorInterface(bot))
