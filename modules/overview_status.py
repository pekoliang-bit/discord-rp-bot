"""總覽用唯讀載入狀態：不聲稱服務健康，不觸碰玩家資料。"""
from modules.overview_helpers import escape
from modules.overview_catalog import SYSTEMS

def command_names(bot):
    return {str(c.name) for c in bot.tree.get_commands()}

def system_lines(bot):
    registered = command_names(bot)
    lines = []
    for group, names in SYSTEMS.items():
        marks = [f"{'✅' if name in registered else '▫️'}`/{escape(name,20)}`" for name in names]
        lines.append(f"**{group}**｜" + "、".join(marks))
    return lines

def status_text(bot):
    names=command_names(bot)
    present=sum(n in names for v in SYSTEMS.values() for n in v)
    total=sum(map(len,SYSTEMS.values()))
    return (f"📌 指令樹目前包含 **{len(names)}** 個頂層指令。\n"
            f"已列入導覽的指令：**{present}/{total}**\n\n"
            "✅ = 本次執行程序的指令樹有註冊；▫️ = 未在頂層指令樹中找到。\n"
            "⚠️ 不代表 Discord 同步完成、功能測試通過或服務正常。")
