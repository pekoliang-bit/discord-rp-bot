
"""Discord RP 模擬網友發文整合驗收。

執行：
    python -m scripts.check_internet_comment_dispatch

不啟動機器人、不連線 Discord、不修改 SQLite。
必須先安裝第 34、37、38 個檔案。
"""

from __future__ import annotations

import asyncio
import sys

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from core.internet_comment_publish_guard import (
    PublishPermission,
)
from modules.internet_comment_bridge import CommentEvent
from modules.internet_comment_review_bridge import (
    CommentPublishApproval,
)
from modules.internet_comment_dispatch import (
    DispatchRequest,
    DispatchServices,
    prepare_checked_comment,
    publish_approved_comment,
)


REQUEST = DispatchRequest(
    guild_id=12345,
    character_id=100,
    channel_id=200,
    event_id="test:event:001",
    approval_id="APPROVAL-001",
    virtual_user_id=300,
    comment_id="C001",
)


@dataclass
class FakeState:
    approved: bool = True
    event_verified: bool = True
    user_active: bool = True
    guild_id: int = 12345
    comment_allowed: bool = True
    text: str = "今天的舞台真的很精彩！"
    calls: int = 0


def make_services(state: FakeState) -> DispatchServices:

    async def load_approval(request):
        return CommentPublishApproval(
            approval_id=request.approval_id,
            guild_id=request.guild_id,
            event_id=request.event_id,
            character_id=request.character_id,
            channel_id=request.channel_id,
            reviewer_id=777,
            approved_at=datetime.now(timezone.utc),
            permission=PublishPermission(
                guild_id=request.guild_id,
                channel_id=request.channel_id,
                character_id=request.character_id,
                event_authorized=state.approved,
                posting_authorized=state.approved,
                simulated_comments_enabled=state.approved,
            ),
        )

    async def load_event(request):
        return CommentEvent(
            event_id=request.event_id,
            category_id=1,
            verified=state.event_verified,
            permitted_comment_ids=(
                frozenset({request.comment_id})
                if state.comment_allowed
                else frozenset()
            ),
        )

    async def load_user(request):
        return {
            "id": request.virtual_user_id,
            "guild_id": state.guild_id,
            "character_id": request.character_id,
            "username": "星光小貓",
            "relation_type": "fan",
            "active": state.user_active,
        }

    async def load_preview(event, comment_id):
        state.calls += 1
        return SimpleNamespace(
            event_id=event.event_id,
            comment_id=comment_id,
            rendered=state.text,
            emotion="開心",
            speaker="粉絲",
        )

    return DispatchServices(
        load_approval=load_approval,
        load_event=load_event,
        load_user=load_user,
        load_preview=load_preview,
    )


async def expect_failure(
    label: str,
    state: FakeState,
    error_type: type[Exception],
) -> None:
    try:
        await prepare_checked_comment(
            REQUEST,
            make_services(state),
        )
    except error_type:
        print(f"[PASS] {label}")
        return

    raise AssertionError(
        f"{label}：未正確拒絕"
    )


async def test_valid_comment() -> None:
    checked = await prepare_checked_comment(
        REQUEST,
        make_services(FakeState()),
    )

    assert checked.guild_id == 12345
    assert checked.channel_id == 200
    assert checked.virtual_user_id == 300
    assert checked.comment_id == "C001"
    assert "模擬網友" in checked.content
    assert "星光小貓" in checked.content

    print("[PASS] 合法留言可以建立安全發文資料")


async def test_permissions() -> None:
    await expect_failure(
        "未核准事件無法發文",
        FakeState(approved=False),
        PermissionError,
    )

    await expect_failure(
        "未核實事件無法發文",
        FakeState(event_verified=False),
        PermissionError,
    )

    await expect_failure(
        "跨伺服器網友無法發文",
        FakeState(guild_id=99999),
        PermissionError,
    )

    await expect_failure(
        "停用網友無法發文",
        FakeState(user_active=False),
        PermissionError,
    )

    await expect_failure(
        "白名單以外評論無法發文",
        FakeState(comment_allowed=False),
        PermissionError,
    )


async def test_content_safety() -> None:
    await expect_failure(
        "禁止全體提及",
        FakeState(text="@everyone 大家快來看"),
        ValueError,
    )

    await expect_failure(
        "禁止 Discord 使用者提及",
        FakeState(text="<@123456789> 大家快來"),
        ValueError,
    )

    await expect_failure(
        "禁止過長留言",
        FakeState(text="測" * 1900),
        ValueError,
    )


async def test_revalidation() -> None:
    state = FakeState()
    services = make_services(state)
    published = []

    async def fake_publisher(
        *,
        channel,
        comment,
        revalidate,
    ):
        # 模擬正式發文前再次驗證。
        checked_again = await revalidate()

        assert checked_again == comment

        published.append(comment)

        return {
            "status": "simulated",
            "message_count": len(published),
        }

    result = await publish_approved_comment(
        channel=object(),
        request=REQUEST,
        services=services,
        publisher=fake_publisher,
    )

    assert result["status"] == "simulated"
    assert len(published) == 1
    assert state.calls == 2

    print("[PASS] 發文前再次確認評論內容")

    # 模擬第一次驗證通過後，
    # 審核被撤銷的情況。
    revoked = FakeState()

    async def revoke_between_checks(
        *,
        channel,
        comment,
        revalidate,
    ):
        revoked.approved = False

        # 發布前的新核准狀態會被拒絕。
        await revalidate()

        raise AssertionError(
            "撤銷核准後不應執行到此"
        )

    try:
        await publish_approved_comment(
            channel=object(),
            request=REQUEST,
            services=make_services(revoked),
            publisher=revoke_between_checks,
        )
    except PermissionError:
        print("[PASS] 審核撤銷後停止發文")
    else:
        raise AssertionError(
            "核准撤銷未阻止發文"
        )


async def run_tests() -> None:
    await test_valid_comment()
    await test_permissions()
    await test_content_safety()
    await test_revalidation()


def main() -> int:
    print("=" * 60)
    print("Discord RP 模擬網友發文整合測試")
    print("=" * 60)

    try:
        asyncio.run(run_tests())
    except Exception as exc:
        print(
            f"[FAIL] {type(exc).__name__}: {exc}"
        )
        return 1

    print()
    print("[PASS] 模擬發文整合驗收完成")
    print(
        "注意：本測試未驗證正式 Discord 權限、"
        "真實發文及 SQLite 交易。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
