import csv
import io
from datetime import UTC, datetime, timedelta

import pytest

from tg_assistant.db.models import (
    PermissionName,
    TelegramChat,
    TelegramChatPermission,
    TelegramChatPolicy,
    TelegramMessage,
)
from tg_assistant.telegram.control_bot import (
    HELP_PAGES,
    LEARNING_CHAT_TYPES,
    ai_followup_keyboard,
    ai_learning_groups_keyboard,
    ai_menu_keyboard,
    ai_provider_keyboard,
    build_groups_csv,
    chunk_chat_summaries,
    digest_window,
    format_chat_summary,
    group_actions_keyboard,
    group_rank_query,
    group_search_query,
    groups_hub_keyboard,
    groups_menu_keyboard,
    markdown_to_telegram_html,
    ollama_menu_keyboard,
    split_telegram_text,
    telegram_html_chunks,
)


def test_group_summary_puts_name_before_id() -> None:
    chat = TelegramChat(
        chat_id=-1001234567890,
        title="Nhóm dự án",
        username="du_an",
        chat_type="supergroup",
    )

    summary = format_chat_summary(chat, allowed=True)

    assert summary.startswith("Tên: Nhóm dự án\n")
    assert "ID: -1001234567890" in summary
    assert "Trạng thái: ALLOW" in summary
    assert "Username: @du_an" in summary
    assert summary.index("Tên:") < summary.index("ID:")


def test_group_summary_has_fallback_name() -> None:
    chat = TelegramChat(chat_id=123, title=None, username=None, chat_type="private")

    assert format_chat_summary(chat, allowed=False).startswith("Tên: Chat riêng không có tên\n")


def test_group_summaries_are_chunked_for_telegram() -> None:
    entries = [f"Tên: Nhóm {index}\nID: {index}" for index in range(20)]

    chunks = chunk_chat_summaries(entries, limit=100)

    assert len(chunks) > 1
    assert all(len(chunk) <= 100 for chunk in chunks)
    assert "Tên: Nhóm 0" in chunks[0]


def test_help_pages_are_complete_and_fit_telegram() -> None:
    help_text = "\n".join(HELP_PAGES)

    assert all(len(page) < 4096 for page in HELP_PAGES)
    for command in (
        "/groups",
        "/permission_set",
        "/search",
        "/ask",
        "/price",
        "/remember",
        "/send",
        "/delete",
        "/task_add",
        "/remind",
        "/confirm",
        "/audit",
    ):
        assert command in help_text


def test_ai_menu_exposes_complete_button_flow() -> None:
    enabled_callbacks = {
        button.callback_data
        for row in ai_menu_keyboard(enabled=True).inline_keyboard
        for button in row
        if button.callback_data
    }
    disabled_callbacks = {
        button.callback_data
        for row in ai_menu_keyboard(enabled=False).inline_keyboard
        for button in row
        if button.callback_data
    }

    assert {
        "ai:ask",
        "ai:search",
        "ai:price",
        "ai:digest:today",
        "ai:digest:yesterday",
        "ai:learn",
        "ai:learn:status",
        "ai:status",
        "ai:toggle:off",
        "ai:help",
    } <= enabled_callbacks
    assert "ai:toggle:on" in disabled_callbacks
    assert "ai:toggle:off" not in disabled_callbacks
    assert all(len(callback) <= 64 for callback in enabled_callbacks | disabled_callbacks)


def test_telegram_exposes_ollama_management_flow() -> None:
    provider_callbacks = {
        button.callback_data
        for row in ai_provider_keyboard(provider="openai").inline_keyboard
        for button in row
        if button.callback_data
    }
    ollama_callbacks = {
        button.callback_data
        for row in ollama_menu_keyboard(active=False).inline_keyboard
        for button in row
        if button.callback_data
    }

    assert "ollama:menu" in provider_callbacks
    assert {
        "ai:provider:set:openai",
        "ai:provider:set:openrouter",
        "ai:provider:set:ollama",
        "ai:provider:set:off",
    } <= provider_callbacks
    assert {
        "ollama:activate",
        "ollama:choose:chat",
        "ollama:choose:embedding",
        "ollama:pull",
        "ollama:choose:delete",
    } <= ollama_callbacks


def test_group_management_exposes_group_ai_toggle() -> None:
    enabled = group_actions_keyboard(-100123, ai_ask_enabled=True)
    disabled = group_actions_keyboard(-100123, ai_ask_enabled=False)
    enabled_callbacks = {
        button.callback_data
        for row in enabled.inline_keyboard
        for button in row
        if button.callback_data
    }
    disabled_callbacks = {
        button.callback_data
        for row in disabled.inline_keyboard
        for button in row
        if button.callback_data
    }

    assert "group_ai:ask_off:-100123" in enabled_callbacks
    assert "group_ai:ask_on:-100123" in disabled_callbacks


def test_ai_learning_group_menu_has_pagination_and_search() -> None:
    rows = [
        (
            TelegramChat(
                chat_id=-(index + 1),
                title=f"Nhóm kiến thức {index + 1}",
                chat_type="group",
            ),
            0,
            0,
            None,
        )
        for index in range(10)
    ]
    rows[0][0].chat_type = "channel"

    keyboard = ai_learning_groups_keyboard(
        rows,
        page=1,
        has_next=True,
        selected_chat_ids={-1, -3},
    )
    callbacks = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    }

    assert sum(value.startswith("ai:learn:toggle:") for value in callbacks) == 10
    assert {
        "ai:learn:page:0",
        "ai:learn:page:2",
        "ai:learn:select_page:1",
        "ai:learn:select_all",
        "ai:learn:search",
        "ai:learn:clear",
        "ai:learn:review",
        "ai:learn:about",
        "ai:menu",
    } <= callbacks
    labels = [button.text for row in keyboard.inline_keyboard for button in row]
    assert sum(label.startswith("✅") for label in labels) == 2
    assert any("📣" in label for label in labels)


def test_ai_followup_and_text_chunking() -> None:
    callbacks = {
        button.callback_data
        for row in ai_followup_keyboard(mode="ask").inline_keyboard
        for button in row
        if button.callback_data
    }
    chunks = split_telegram_text("x" * 8001, limit=3900)

    assert callbacks == {"ai:ask", "ai:menu"}
    assert "".join(chunks) == "x" * 8001
    assert all(len(chunk) <= 3900 for chunk in chunks)


def test_ai_markdown_is_converted_to_safe_telegram_html() -> None:
    markdown = (
        "## Bản tin tổng hợp\n\n"
        "### Thị trường\n"
        "- Thanh lý khoảng **250 triệu USD**. [S6]\n"
        "- Dùng `pending_action` chỉ như văn bản.\n"
        "<script>không được chạy</script>\n"
        "[Bài gốc](https://t.me/example/123)"
    )

    rendered = markdown_to_telegram_html(markdown)

    assert "<b>Bản tin tổng hợp</b>" in rendered
    assert "<b>Thị trường</b>" in rendered
    assert "• Thanh lý khoảng <b>250 triệu USD</b>. [S6]" in rendered
    assert "<code>pending_action</code>" in rendered
    assert "&lt;script&gt;" in rendered
    assert '<a href="https://t.me/example/123">Bài gốc</a>' in rendered
    assert "##" not in rendered
    assert "**" not in rendered


def test_telegram_html_chunks_keep_complete_formatting_tags() -> None:
    markdown = "\n\n".join(
        f"### Mục {index}\n- Nội dung **quan trọng** số {index} " + ("x" * 500)
        for index in range(20)
    )

    chunks = telegram_html_chunks(markdown, limit=1200)

    assert len(chunks) > 1
    assert all(len(chunk) <= 1200 for chunk in chunks)
    assert all(chunk.count("<b>") == chunk.count("</b>") for chunk in chunks)
    assert all("###" not in chunk and "**" not in chunk for chunk in chunks)


def test_digest_window_uses_exact_vietnam_calendar_days() -> None:
    now = datetime(2026, 7, 24, 8, 30, tzinfo=UTC)

    today_start, today_end = digest_window("today", now=now)
    yesterday_start, yesterday_end = digest_window("yesterday", now=now)

    assert today_start == datetime(2026, 7, 23, 17, 0, tzinfo=UTC)
    assert today_end == now
    assert yesterday_start == datetime(2026, 7, 22, 17, 0, tzinfo=UTC)
    assert yesterday_end == today_start


@pytest.mark.asyncio
async def test_group_ranking_uses_outgoing_interactions_and_excludes_private(session) -> None:
    now = datetime.now(UTC)
    active = TelegramChat(
        chat_id=-1001,
        title="Nhóm tích cực",
        username="active_group",
        chat_type="supergroup",
        last_seen_at=now,
    )
    quiet = TelegramChat(
        chat_id=-2,
        title="Nhóm yên tĩnh",
        chat_type="group",
        last_seen_at=now + timedelta(days=1),
    )
    private = TelegramChat(
        chat_id=3,
        title="Chat riêng",
        chat_type="private",
        last_seen_at=now + timedelta(days=2),
    )
    channel = TelegramChat(
        chat_id=-4,
        title="Kênh tin tức",
        username="news_channel",
        chat_type="channel",
        last_seen_at=now + timedelta(days=3),
    )
    session.add_all([active, quiet, private, channel])
    await session.flush()
    session.add_all(
        [
            TelegramChatPolicy(chat_id=-1001, allowed=True),
            TelegramChatPermission(
                chat_id=-1001,
                permission=PermissionName.GROUP_AI_ASK.value,
                enabled=True,
            ),
            TelegramChatPolicy(chat_id=-2, allowed=True),
            TelegramChatPermission(
                chat_id=-2,
                permission=PermissionName.READ_MESSAGES.value,
                enabled=True,
            ),
            TelegramMessage(
                chat_id=-1001,
                message_id=1,
                text="outgoing",
                sent_at=now,
                is_outgoing=True,
            ),
            TelegramMessage(
                chat_id=-1001,
                message_id=2,
                text="incoming",
                sent_at=now,
                is_outgoing=False,
            ),
            TelegramMessage(
                chat_id=-2,
                message_id=1,
                text="incoming",
                sent_at=now,
                is_outgoing=False,
            ),
        ]
    )
    await session.flush()

    rows = list((await session.execute(group_rank_query(limit=10))).all())

    assert [row[0].chat_id for row in rows] == [-1001, -2]
    assert rows[0].outgoing_messages == 1
    assert rows[0].synced_messages == 2
    second_page = list((await session.execute(group_rank_query(limit=1, offset=1))).all())
    assert second_page[0][0].chat_id == -2
    ai_rows = list((await session.execute(group_rank_query(category="ai"))).all())
    permission_rows = list(
        (await session.execute(group_rank_query(category="permissions"))).all()
    )
    assert [row[0].chat_id for row in ai_rows] == [-1001]
    assert [row[0].chat_id for row in permission_rows] == [-2]
    search_results = list(
        (await session.scalars(group_search_query("@active_group", limit=10))).all()
    )
    assert [chat.chat_id for chat in search_results] == [-1001]
    learning_rows = list(
        (await session.execute(group_rank_query(limit=10, chat_types=LEARNING_CHAT_TYPES))).all()
    )
    assert [row[0].chat_id for row in learning_rows] == [-1001, -2, -4]
    learning_search_results = list(
        (
            await session.scalars(
                group_search_query(
                    "@news_channel",
                    limit=10,
                    chat_types=LEARNING_CHAT_TYPES,
                )
            )
        ).all()
    )
    assert [chat.chat_id for chat in learning_search_results] == [-4]


def test_groups_menu_has_pagination_without_exceeding_ten_groups() -> None:
    rows = [
        (
            TelegramChat(
                chat_id=-(index + 1),
                title=f"Nhóm {index + 1}",
                chat_type="group",
            ),
            0,
            0,
            None,
        )
        for index in range(10)
    ]

    keyboard = groups_menu_keyboard(rows, page=1, has_next=True)
    callback_data = [
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    ]

    assert sum(value.startswith("group:open:") for value in callback_data) == 10
    assert "groups:search" in callback_data
    assert "groups:page:0" in callback_data
    assert "groups:page:2" in callback_data


def test_groups_hub_has_three_management_categories() -> None:
    callbacks = {
        button.callback_data
        for row in groups_hub_keyboard().inline_keyboard
        for button in row
        if button.callback_data
    }

    assert {
        "groups:list:ai",
        "groups:list:permissions",
        "groups:list:all",
        "groups:search",
        "groups:export_csv",
    } <= callbacks


def test_filtered_groups_menu_preserves_category_during_pagination() -> None:
    rows = [
        (
            TelegramChat(chat_id=-1, title="Nhóm AI", chat_type="group"),
            0,
            0,
            None,
        )
    ]

    keyboard = groups_menu_keyboard(
        rows,
        page=1,
        has_next=True,
        category="ai",
    )
    callbacks = {
        button.callback_data
        for row in keyboard.inline_keyboard
        for button in row
        if button.callback_data
    }

    assert "groups:page:ai:0" in callbacks
    assert "groups:page:ai:2" in callbacks
    assert "groups:show" in callbacks


def test_groups_csv_is_utf8_and_blocks_spreadsheet_formulas() -> None:
    chat = TelegramChat(
        chat_id=-1001,
        title='=WEBSERVICE("https://example.invalid")',
        username="public_group",
        chat_type="supergroup",
    )
    policy = TelegramChatPolicy(chat_id=-1001, allowed=True)

    content = build_groups_csv(
        [(chat, 3, 10, datetime(2026, 7, 24, tzinfo=UTC))],
        {-1001: policy},
    )
    rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))

    assert rows[0][1] == "Tên nhóm"
    assert rows[1][1].startswith("'=")
    assert rows[1][4] == "'@public_group"
    assert rows[1][5] == "ALLOW"
