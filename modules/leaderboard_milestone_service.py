"""依已封存的30天快照計算獲獎次數，僅記錄待派發。"""
from core.database import get_connection
from modules.leaderboard_milestone_rules import reached
from modules.leaderboard_milestone_storage import store

def _count(db, guild, entity, board, idx, rank, kind):
    base = """SELECT COUNT(*) FROM lb_v11_snapshots
              WHERE guild_id=? AND cycle='30d' AND idx<=? AND entity_key=?"""
    args = [str(guild), idx, str(entity)]
    if kind != "cross_top3":
        base += " AND board=?"
        args.append(board)
    if kind in ("board_top3", "cross_top3"):
        base += " AND rank<=3"
    elif kind == "same_rank":
        base += " AND rank=?"
        args.append(rank)
    return int(db.execute(base, args).fetchone()[0])

def _streak(db, guild, entity, board, idx, rank):
    count = 0
    while idx > 0:
        found = db.execute("""SELECT 1 FROM lb_v11_snapshots
          WHERE guild_id=? AND cycle='30d' AND idx=? AND entity_key=?
            AND board=? AND rank=? LIMIT 1""",
          (str(guild),idx,str(entity),board,rank)).fetchone()
        if not found:
            break
        count += 1
        idx -= 1
    return count

def stage_milestones(guild_id, idx, board, ranked_rows):
    """要求該期快照已存在；跨榜統計按相同 entity_key，絕不跨身份合併。"""
    if idx < 1:
        raise ValueError("期數必須大於零")
    with get_connection() as db:
        for row in ranked_rows:
            entity, rank = str(row["entity_key"]), int(row["rank"])
            for kind in ("same_rank","board_top3","cross_top3","streak"):
                if kind in ("board_top3","cross_top3") and rank > 3:
                    continue
                if kind == "streak":
                    current = _streak(db,guild_id,entity,board,idx,rank)
                    previous = max(0,current-1)
                else:
                    current = _count(db,guild_id,entity,board,idx,rank,kind)
                    previous = current-1
                dimension = str(rank) if kind in ("same_rank","streak") else "top3"
                # 跨榜同一獲獎身份只計一次，避免多榜同一期重複給里程碑。
                ledger_board = "*" if kind == "cross_top3" else board
                rewards = reached(kind,previous,current)
                if rewards:
                    store(guild_id,entity,ledger_board,kind,dimension,idx,rewards)
