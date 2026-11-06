"""正式榜單身分類型：禁止跨單位錯發或舊式裸 ID。"""
from modules.leaderboard_entity_identity import parse_identity
BOARD_TYPES = {"industry":{"company"},"groups":{"group"},"shops":{"shop"},"factions":{"faction"},"cp":{"cp"},"social":{"social_account"}}
CHARACTER_BOARDS = {"impact","wealth","heat","commercial","public_favor","pr","appearance","intellect","main_skill","acting","dance","sing","hosting","variety","talent","fans","antis","awareness","works","endorsements","newcomer"}
def expected_type(board):
    board=str(board)
    if board=='awards':raise ValueError('第28榜尚未開放獎勵')
    if board in CHARACTER_BOARDS:return 'character'
    matches=BOARD_TYPES.get(board)
    if matches and len(matches)==1:return next(iter(matches))
    raise ValueError('未知排行榜類型')
def validate_identity(board,entity_key):
    expected=expected_type(board)
    parsed=parse_identity(entity_key)
    if parsed is None:raise ValueError('舊式或無類型身分不可直接入帳')
    if parsed['entity_type']!=expected:raise ValueError(f'{board} 不接受 {parsed["entity_type"]} 身分')
    return parsed
