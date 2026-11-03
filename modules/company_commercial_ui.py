"""公司商務管理介面。

/公司 → 商務管理
不註冊新的 Slash 指令。
"""

from __future__ import annotations

import asyncio
import logging

import discord

from core.company_career_progress import (
    career_progress,
    format_career_progress,
)
from core.company_endorsement_service import (
    appoint_endorsement,
    end_endorsement,
)
from core.company_vip_service import set_brand_vip
from core.company_brand_exposure import (
    record_endorsement_exposure,
)

LOG = logging.getLogger(__name__)


async def respond(i, message):
    if i.response.is_done():
        await i.followup.send(
            str(message)[:1900],
            ephemeral=True,
        )
    else:
        await i.response.send_message(
            str(message)[:1900],
            ephemeral=True,
        )


class CommercialModal(discord.ui.Modal):
    def __init__(self, gid, uid, action):
        titles = {
            "career": "查看職位經驗",
            "appoint": "任命品牌代言",
            "end": "結束品牌代言",
            "vip": "設定品牌VIP",
            "exposure": "登記代言曝光",
        }

        super().__init__(
            title=titles[action],
            timeout=300,
        )

        self.gid = str(gid)
        self.uid = str(uid)
        self.action = action
        self.inputs = []

        specs = {
            "career": [
                ("公司編號", "business"),
                ("員工角色編號", "character"),
            ],
            "appoint": [
                ("品牌編號", "business"),
                ("代言角色編號", "character"),
                ("代言等級（4至10）", "tier"),
                ("代言範圍", "scope"),
            ],
            "end": [
                ("代言紀錄編號", "record"),
            ],
            "vip": [
                ("品牌編號", "business"),
                ("會員角色編號", "character"),
                ("VIP等級（1至4）", "level"),
            ],
            "exposure": [
                ("代言紀錄編號", "record"),
                ("曝光級別（1至4）", "level"),
                ("事件唯一編號", "event"),
            ],
        }

        self.names = []

        for label, name in specs[action]:
            field = discord.ui.TextInput(
                label=label,
                max_length=150,
                required=True,
            )
            self.add_item(field)
            self.inputs.append(field)
            self.names.append(name)

    async def on_submit(self, interaction):
        if (
            str(interaction.guild_id) != self.gid
            or str(interaction.user.id) != self.uid
        ):
            return await respond(
                interaction,
                "這不是你的商務管理表單。",
            )

        values = {
            name: field.value.strip()
            for name, field in zip(
                self.names, self.inputs
            )
        }

        await interaction.response.defer(
            ephemeral=True,
            thinking=True,
        )

        try:
            if self.action == "career":
                data = await asyncio.to_thread(
                    career_progress,
                    self.gid,
                    values["business"],
                    int(values["character"]),
                )
                message = format_career_progress(data)

            elif self.action == "appoint":
                result = await asyncio.to_thread(
                    appoint_endorsement,
                    self.gid, values["business"],int(values["character"]),
                    int(values["tier"]),values["scope"],self.uid,
                )
                if result['status']=='pending':
                    message = (f"📨 代言邀約 #{result['id']} 已送出。\n{result['title']}\n"
                               "藝人持有人須至 /公司 → 商務管理 → 回覆任命 才會正式生效。")
                else:
                    message = (f"✅ 代言 #{result['id']} 已建立\n{result['title']}\n"
                               f"競品風險：{result['risk']}\n尚未確認的合約條款須另行提出及同意。")

            elif self.action == "end":
                message = await asyncio.to_thread(
                    end_endorsement,
                    self.gid,
                    int(values["record"]),
                    self.uid,
                )

            elif self.action == "vip":
                message = await asyncio.to_thread(
                    set_brand_vip,
                    self.gid,
                    values["business"],
                    int(values["character"]),
                    int(values["level"]),
                    self.uid,
                )

            elif self.action == "exposure":
                result = await asyncio.to_thread(
                    record_endorsement_exposure,
                    self.gid,
                    int(values["record"]),
                    int(values["level"]),
                    values["event"],
                    self.uid,
                )

                message = (
                    "ℹ️ 此曝光事件已記錄過。"
                    if result["duplicate"]
                    else (
                        "✅ 曝光熱度已記錄："
                        f"{result['points']:,}點。"
                    )
                )

        except (
            ValueError,
            PermissionError,
            LookupError,
            RuntimeError,
        ) as exc:
            message = f"⚠️ {exc}"

        except Exception:
            LOG.exception(
                "公司商務操作失敗：%s",
                self.action,
            )
            message = (
                "⚠️ 商務操作未確認完成，"
                "請管理員核對資料。"
            )

        await interaction.followup.send(
            str(message)[:1900],
            ephemeral=True,
        )


class CompanyCommercialView(discord.ui.View):
    def __init__(self, gid, uid):
        super().__init__(timeout=300)
        self.gid = str(gid)
        self.uid = str(uid)

        items = [
            ("endorsement_history", "📜 代言紀錄"),
            ("career", "📈 職位經驗"),
            ("appoint", "🎤 任命代言"),
            ("reply_invite", "📨 回覆任命"),
            ("end", "📜 結束代言"),
            ("vip", "💎 品牌VIP"),
            ("exposure", "📣 代言曝光"),
        ]

        from modules.company_endorsement_finance_ui import EndorsementFinanceEntry
        self.add_item(EndorsementFinanceEntry())
        from modules.company_contract_lifecycle_ui import TermsEntry
        self.add_item(TermsEntry())

        for action, label in items:
            self.add_item(
                CommercialActionButton(
                    action, label
                )
            )

    async def interaction_check(self, interaction):
        allowed = (
            str(interaction.guild_id) == self.gid
            and str(interaction.user.id) == self.uid
        )

        if not allowed:
            await respond(
                interaction,
                "這不是你的商務管理面板。",
            )

        return allowed


class CommercialActionButton(discord.ui.Button):
    def __init__(self, action, label):
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
        )
        self.action = action

    async def callback(self, interaction):
        view = self.view
        if self.action == "reply_invite":
            await interaction.response.send_message("📨 代言邀約：先查看待回覆紀錄，再填寫邀約編號。",view=AppointmentReplyView(view.gid,view.uid),ephemeral=True)
            return
        if self.action == "endorsement_history":
            await interaction.response.send_modal(EndorsementHistoryModal(view.gid, view.uid))
            return
        await interaction.response.send_modal(
            CommercialModal(
                view.gid,
                view.uid,
                self.action,
            )
        )


class CompanyCommercialEntry(discord.ui.Button):
    def __init__(self):
        super().__init__(
            label="📊 商務管理",
            style=discord.ButtonStyle.secondary,
            row=3,
        )

    async def callback(self, interaction):
        panel = self.view

        if (
            str(interaction.guild_id) != panel.gid
            or str(interaction.user.id) != panel.uid
        ):
            return await respond(
                interaction,
                "這不是你的公司面板。",
            )

        await interaction.response.send_message(
            "📊 **公司商務管理中心**\n"
            "職位經驗、品牌代言、VIP及曝光紀錄。",
            view=CompanyCommercialView(
                panel.gid,
                panel.uid,
            ),
            ephemeral=True,
        )


class EndorsementHistoryModal(discord.ui.Modal):
    def __init__(self, gid, uid):
        super().__init__(title="查詢品牌代言紀錄", timeout=300)
        self.gid, self.uid = str(gid), str(uid)
        self.brand = discord.ui.TextInput(label="品牌編號", max_length=100)
        self.add_item(self.brand)

    async def on_submit(self, interaction):
        if str(interaction.guild_id) != self.gid or str(interaction.user.id) != self.uid:
            return await respond(interaction, "這不是你的查詢表單。")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            from core.database import get_connection
            from core.company_endorsement_store import initialize_endorsements, active_endorsements
            from modules.company_business import has_permission
            def load():
                with get_connection() as db:
                    brand = db.execute("SELECT owner_id FROM rp_business_entities WHERE guild_id=? AND business_id=? AND kind='brand' AND status='active'", (self.gid,self.brand.value.strip())).fetchone()
                    if not brand or (str(brand['owner_id']) != self.uid and not has_permission(db,self.gid,self.brand.value.strip(),self.uid,'manage')):
                        raise PermissionError('沒有這個品牌的管理權限')
                    initialize_endorsements(db)
                    return active_endorsements(db,self.gid,brand_id=self.brand.value.strip())[:20]
            rows = await asyncio.to_thread(load)
            lines = ["📜 **有效代言紀錄**"]
            lines.extend(f"• #{r['id']}｜角色 {r['character_id']}｜T{r['tier']}｜{r['scope']}" for r in rows)
            if not rows: lines.append("目前沒有有效代言")
            await interaction.followup.send("\n".join(lines)[:1900],ephemeral=True)
        except (PermissionError, ValueError) as e:
            await interaction.followup.send(f"⚠️ {e}",ephemeral=True)
        except Exception:
            LOG.exception('代言紀錄查詢失敗')
            await interaction.followup.send('⚠️ 代言紀錄暫時無法讀取。',ephemeral=True)


class AppointmentReplyModal(discord.ui.Modal):
    def __init__(self,gid,uid):
        super().__init__(title="回覆代言任命",timeout=300)
        self.gid,self.uid=str(gid),str(uid)
        self.invite=discord.ui.TextInput(label="任命邀約編號",max_length=20)
        self.answer=discord.ui.TextInput(label="接受／拒絕",max_length=2)
        self.add_item(self.invite);self.add_item(self.answer)
    async def on_submit(self,i):
        if str(i.guild_id)!=self.gid or str(i.user.id)!=self.uid:
            return await i.response.send_message("非本人表單",ephemeral=True)
        await i.response.defer(ephemeral=True,thinking=True)
        try:
            from core.company_endorsement_appointment import decide
            answer=self.answer.value.strip()
            if answer not in ('接受','拒絕'): raise ValueError('只能填「接受」或「拒絕」')
            result=await asyncio.to_thread(decide,self.gid,int(self.invite.value),self.uid,answer=='接受')
            msg=(f"✅ 已接受邀約，正式代言編號 #{result['id']}" if result['status']=='accepted'
                 else '已拒絕代言邀約')
        except (ValueError,PermissionError) as exc: msg=f'⚠️ {exc}'
        except Exception:
            LOG.exception('代言任命回覆失敗');msg='⚠️ 無法回覆邀約，請查看日誌'
        await i.followup.send(msg,ephemeral=True)

class AppointmentReplyView(discord.ui.View):
    def __init__(self,gid,uid):
        super().__init__(timeout=300);self.gid,self.uid=str(gid),str(uid)
    async def interaction_check(self,i):
        if str(i.guild_id)!=self.gid or str(i.user.id)!=self.uid:
            await i.response.send_message('非本人面板',ephemeral=True);return False
        return True
    @discord.ui.button(label='📬 待回覆任命',style=discord.ButtonStyle.secondary)
    async def list_invites(self,i,b):
        await i.response.defer(ephemeral=True,thinking=True)
        try:
            from core.company_endorsement_appointment import pending
            rows=await asyncio.to_thread(pending,self.gid,self.uid)
            msg='\n'.join(f"#{r['id']}｜{r['brand_id']}｜角色#{r['character_id']}｜T{r['tier']}｜{r['scope']}" for r in rows) or '目前沒有待回覆任命'
        except Exception:
            LOG.exception('讀取任命邀約失敗');msg='⚠️ 讀取失敗'
        await i.followup.send(msg[:1900],ephemeral=True)
    @discord.ui.button(label='✅ 回覆邀約',style=discord.ButtonStyle.primary)
    async def respond_invite(self,i,b):
        await i.response.send_modal(AppointmentReplyModal(self.gid,self.uid))
