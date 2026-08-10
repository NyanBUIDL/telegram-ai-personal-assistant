from __future__ import annotations

import csv
import io
from datetime import UTC, datetime

import pytest

from tg_assistant.db.models import (
    AppSetting,
    BackgroundJob,
    TelegramChat,
    TelegramMessage,
)
from tg_assistant.policy import PolicyEngine
from tg_assistant.services.knowledge_inventory import (
    build_learning_inventory_csv,
    parse_learning_inventory_csv,
    reconcile_knowledge_sources,
)


@pytest.mark.asyncio
async def test_inventory_classifies_learned_not_learned_and_no_content(session) -> None:
    learned_id, not_learned_id, no_content_id = -1001, -1002, -1003
    session.add_all(
        [
            TelegramChat(chat_id=learned_id, title="Đã học", chat_type="group"),
            TelegramChat(chat_id=not_learned_id, title="Chưa học", chat_type="channel"),
            TelegramChat(chat_id=no_content_id, title="Không có nội dung", chat_type="group"),
        ]
    )
    await session.flush()
    await PolicyEngine().apply_template(session, learned_id, "knowledge")
    session.add(
        TelegramMessage(
            chat_id=learned_id,
            message_id=1,
            text="nội dung đã lập chỉ mục",
            sent_at=datetime.now(UTC),
        )
    )
    session.add(
        AppSetting(
            key=f"knowledge_checkpoint:{learned_id}",
            value=1,
        )
    )
    session.add_all(
        [
            BackgroundJob(
                job_type="learn_group",
                status="completed",
                payload={"chat_id": learned_id, "indexed_count": 1},
            ),
            BackgroundJob(
                job_type="learn_group",
                status="completed",
                payload={"chat_id": no_content_id, "indexed_count": 0},
            ),
        ]
    )
    await session.flush()

    rows = await reconcile_knowledge_sources(session)
    statuses = {row.chat.chat_id: row.source.status for row in rows}

    assert statuses == {
        learned_id: "learned",
        not_learned_id: "not_learned",
        no_content_id: "no_content",
    }

    csv_rows = list(
        csv.DictReader(io.StringIO(build_learning_inventory_csv(rows).decode("utf-8-sig")))
    )
    labels = {row["chat_id"]: row["trang_thai"] for row in csv_rows}
    assert labels[f"'{learned_id}"] == "Đã học"
    assert labels[f"'{not_learned_id}"] == "Chưa học"
    assert labels[f"'{no_content_id}"] == "Không học được - không lấy được nội dung"


def test_inventory_csv_accepts_user_notes_and_exact_excel_chat_ids() -> None:
    content = (
        "\ufeffchat_id,can_hoc,ghi_chu\n"
        '"\'-1001315055119",CO,"ưu tiên học lại"\n'
        '"-1,001,315,055,120.00",KHONG,"không cần"\n'
    ).encode()

    edits = parse_learning_inventory_csv(content)

    assert edits[0].chat_id == -1001315055119
    assert edits[0].should_learn
    assert edits[0].note == "ưu tiên học lại"
    assert edits[1].chat_id == -1001315055120
    assert not edits[1].should_learn


def test_inventory_csv_rejects_unknown_learning_marker() -> None:
    content = b"chat_id,can_hoc,ghi_chu\n-1001,MAYBE,test\n"

    with pytest.raises(ValueError, match="can_hoc"):
        parse_learning_inventory_csv(content)
