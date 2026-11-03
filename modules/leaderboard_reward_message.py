
"""排行榜結算通知文字格式。"""


def settlement_message(cycle, index, board_name, rows):
    if cycle not in ("14d", "30d"):
        raise ValueError("未知結算週期")

    label = (
        "14天小結算"
        if cycle == "14d"
        else "30天大結算"
    )

    lines = [
        f"🏆 {board_name}｜{label}",
        f"第 {int(index)} 期",
        "",
    ]

    for row in list(rows)[:10]:
        rank = int(row["rank"])
        name = str(
            row.get("display_name", "未知參賽者")
        )
        lines.append(f"第 {rank} 名｜{name}")

    if cycle == "14d":
        lines.append("")
        lines.append("本期僅公布排名，不發放獎勵。")
    else:
        lines.append("")
        lines.append(
            "本期獎勵依正式派發狀態另行處理。"
        )

    return "\n".join(lines)


def private_reward_message(board_name, rank, status):
    labels = {
        "pending_adapter": "等待獎勵入帳系統",
        "unconfigured": "獎勵規則尚未配置",
        "paid": "已完成入帳",
        "blocked": "暫停派發",
    }

    state = labels.get(status, str(status))

    return (
        f"🏆 排行榜結算通知\n"
        f"榜單：{board_name}\n"
        f"名次：第 {int(rank)} 名\n"
        f"獎勵狀態：{state}"
    )
