"""第26階段：產生可供未來通知系統使用的訊息資料；不發送 Discord。"""
from __future__ import annotations

def make_action_notice(*,proposal_id,action_type,outcome,target_character_id,
                       source_faction_name=None,source_disclosed=False):
    if action_type not in ('sabotage','defense'):
        raise ValueError('未知行動類型。')
    if outcome not in ('success','failed','blocked','expired','revoked'):
        raise ValueError('未知結果類型。')
    public_source=str(source_faction_name) if source_disclosed and source_faction_name else None
    return {'event_key':f'faction-action:{int(proposal_id)}:{outcome}',
            'category':'character_private','action_type':action_type,
            'outcome':outcome,'target_character_id':int(target_character_id),
            'source_faction':public_source,'source_hidden':public_source is None,
            'sent':False,'execution_enabled':False}
