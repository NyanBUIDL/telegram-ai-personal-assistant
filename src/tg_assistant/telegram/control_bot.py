from __future__ import annotations

import asyncio
import csv
import html
import io
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.methods import GetMe, GetUpdates
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InaccessibleMessage,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import case, event, func, or_, select, text

from ..ai.rag import (
    SourceEvidence,
    citation_appendix,
    rag_time_window,
    source_context,
    telegram_message_url,
)
from ..config import get_settings, save_settings_env
from ..db.base import folded_contains
from ..db.models import (
    AiMemory,
    AppSetting,
    AuditLog,
    BackgroundJob,
    KnowledgeSource,
    ModerationRule,
    PendingAction,
    PermissionName,
    Project,
    Reminder,
    RuntimeMetric,
    Summary,
    Task,
    TaskStatus,
    TelegramChat,
    TelegramChatPermission,
    TelegramChatPolicy,
    TelegramMessage,
)
from ..policy import PolicyContext, PolicyEngine
from ..security import contains_secret, redact
from ..services.actions import MAX_BULK_LEARNING_SOURCES, PendingActionService
from ..services.coingecko import (
    CoinGeckoClient,
    CoinGeckoError,
    extract_price_asset,
    format_coin_price,
)
from ..services.daily_digest import (
    digest_citation_priority,
    digest_coverage_text,
    digest_source_list,
    load_daily_digest_dataset,
    prefer_primary_citations,
    referenced_item_indexes,
    require_digest_authorization,
)
from ..services.knowledge_inventory import (
    build_learning_inventory_csv,
    inventory_counts,
    parse_learning_inventory_csv,
    reconcile_knowledge_sources,
)
from ..services.memory import MemoryService
from ..services.ollama import (
    OllamaError,
    OllamaModel,
    OllamaService,
    format_model_size,
    validate_model_name,
)
from ..services.operations import cleanup_storage, collect_runtime_metric
from ..services.revocation import AuthorizationRevoked, validate_answer
from ..services.search import SearchService
from ..services.tasks import TaskService
from ..services.timeparse import parse_vietnamese_datetime
from .pairing import PairingCode

if TYPE_CHECKING:
    from ..ai.budget import BudgetService
    from ..ai.engine import AiEngine
    from ..ai.rag import RagService


GROUP_CHAT_TYPES = ("group", "supergroup")


@dataclass(frozen=True, slots=True)
class _AuthorizedDigest:
    text: str
    epochs: tuple[tuple[int, int], ...]


LEARNING_CHAT_TYPES = ("group", "supergroup", "channel")
LEARNING_SELECTION_LIMIT = MAX_BULK_LEARNING_SOURCES
GROUP_LIST_CATEGORIES = {"ai", "permissions", "all"}
OllamaActivateHandler = Callable[[str, str, int], Awaitable[str]]
AiProviderSwitchHandler = Callable[[str], Awaitable[str]]


def format_bytes(value: int | None) -> str:
    if value is None:
        return "-"
    sign = "-" if value < 0 else ""
    amount = float(abs(value))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{sign}{amount:.1f} {unit}"
        amount /= 1024
    return f"{sign}{amount:.1f} TB"


class GroupSearchState(StatesGroup):
    waiting_query = State()


class AiFlowState(StatesGroup):
    waiting_question = State()
    waiting_price = State()
    waiting_search = State()
    waiting_learning_search = State()
    waiting_learning_audit_file = State()
    selecting_learning_groups = State()
    waiting_source_limits = State()


class OllamaFlowState(StatesGroup):
    waiting_pull_model = State()


HELP_PAGES = (
    """TRỢ LÝ TELEGRAM CÁ NHÂN

Bot chỉ phản hồi tài khoản chủ sở hữu. Chat mặc định bị BLOCK; thay đổi quyền và hành động quan trọng phải được /confirm.

1. CHAT VÀ GROUP
/groups — mở 3 danh mục: nhóm đã bật AI, nhóm có quyền khác và tất cả nhóm
Mỗi danh mục hiện tối đa 10 nhóm mỗi trang; vẫn có tìm theo tên/username và xuất CSV
/group_info <chat_id> — thông tin một chat
/group_allow <chat_id> — đưa chat vào allowlist
/group_block <chat_id> <keep|archive|delete> — chặn chat và xử lý memory
Trong flow group: “Bật AUTO xóa link non-admin” chỉ xóa tin mới có đường dẫn do thành viên thường đăng.

2. QUYỀN
/permissions <chat_id> — xem trạng thái mọi quyền
/permission_template <chat_id> <mẫu>
Mẫu: read_only | knowledge | task_management | moderation
/permission_set <chat_id> <permission> <on|off>

Các quyền: read_messages, sync_history, monitor_new_messages, search_messages, summarize, extract_tasks, create_memories, send_messages, edit_own_messages, delete_own_messages, delete_any_messages, pin_messages, moderate_messages, download_media, analyze_files, auto_knowledge, auto_task_suggestion, auto_moderation, group_ai_ask.

Trong flow quản lý group, “Bật thành viên hỏi AI” cho phép thành viên dùng
@your_assistant_username /ask <câu hỏi>. AI trả lời ngay trong group từ kho dự án đã học,
chỉ kèm nguồn khi câu hỏi mang tính tin tức hoặc cập nhật mới. Câu hỏi như
“nhóm này đang thảo luận gì?” chỉ đọc hội thoại của chính group đang gọi.
Câu “@a_member đã nói về chủ đề gì?” phân giải đúng tài khoản và truy cứu tối đa
100 tin gần nhất của riêng tài khoản đó trong group, đồng thời lưu vào SQLite.

“AI mode, fallback & quota” cho phép cấu hình riêng từng group:
inherit, LOCAL ONLY, local→cloud, cloud only, cloud→local hoặc tắt AI group.
Tại đây cũng chọn OpenAI/OpenRouter, retention 7/30 ngày và quota
message/vector/MB cho nguồn.""",
    """3. TÌM KIẾM, TÓM TẮT VÀ AI
/search <từ khóa> — mặc định tìm trong 7 ngày gần nhất từ dữ liệu local đã được cấp quyền
Bộ lọc: in:<chat_id>, from:<user_id>, after:YYYY-MM-DD, before:YYYY-MM-DD, has:link|file|image|document|audio, type:task|decision
/ask <câu hỏi> — hỏi AI dựa trên nguồn Telegram được phép trong 7×24 giờ gần nhất
/price <mã hoặc tên coin> — xem giá USD/VND, biến động 24h, vốn hóa và khối lượng từ CoinGecko
/digest [today|yesterday] — tạo bản tin gần đây

Câu trả lời AI có mã nguồn [S1], [S2]... và phần DẪN CHỨNG. Bot ưu tiên link
bài viết Telegram; nếu không tạo được link thì ghi tên group/channel và thời gian đăng.
Mốc 7 ngày là cửa sổ trượt tính ngược chính xác từ lúc bạn hỏi; bản tin hôm nay/hôm qua
vẫn dùng đúng ngày lịch theo giờ Việt Nam.

/ai_status — trạng thái, model và ngân sách
/ai_usage — mức sử dụng AI
/ai_budget — ngân sách AI
/ai_model — model hiện tại
/ai_on — bật lớp AI
/ai_off — tắt toàn bộ AI; tìm kiếm local/SQLite vẫn hoạt động

Trong menu AI, chỉ cần bấm nút để hỏi AI, tìm local, tóm tắt hôm nay/hôm qua,
học từ nhiều group/channel, xem ngân sách hoặc bật/tắt AI. “Học từ group/channel”
cho phép chọn nhiều nguồn và dùng RAG/embedding, không phải fine-tune model. Sau mỗi kết quả có
nút hỏi/tìm tiếp.

“Xuất kiểm kê nguồn học” tạo CSV gồm nguồn đã học, chưa học, không lấy được nội dung
và nguồn lỗi. Điền CO ở cột can_hoc, thêm ghi chú rồi gửi lại file cho bot để tạo
hàng đợi học sau bước xác nhận.

“Tiến độ học” có nút Tạm dừng học/Tiếp tục học. Job đang chạy sẽ dừng an toàn
tại ranh giới giữa đồng bộ và embedding.

“Vận hành & lưu trữ” hiển thị RAM, VRAM, CPU, dung lượng tiến trình, vector/media,
tăng trưởng lưu trữ và hàng đợi worker. Storage cleanup luôn có bước xem trước,
sau đó mới xóa message hết retention, dữ liệu vượt quota và orphan vector.

Bạn cũng có thể gửi câu hỏi tự nhiên không có dấu /; bot sẽ dùng AI hoặc tìm kiếm local tùy cấu hình.

4. MEMORY
/remember [private|global|chat:<id>] <nội dung> — đề nghị ghi nhớ
/memory — liệt kê memory gần đây
/memory <từ khóa> — tìm memory
/memory_search [chat:<id>] <từ khóa> — tìm memory theo phạm vi
/memory_forget <memory_id> — đề nghị quên memory.""",
    """5. TIN NHẮN TELEGRAM
/send <chat_id> <nội dung> — gửi tin
/edit <chat_id> <message_id> <nội dung mới> — sửa tin do bạn gửi
/pin <chat_id> <message_id> — ghim tin
/delete <chat_id> <message_id> <own|any> [lý do] — xóa tin

Các lệnh trên tạo bản xem trước và cần xác nhận:
/pending_actions — xem hành động đang chờ
/confirm <action_id> — xác nhận
/cancel <action_id> — hủy
/audit [chat_id] — xem nhật ký hành động

6. TASK, PROJECT VÀ NHẮC VIỆC
/task_add <nội dung> [| thời gian tiếng Việt]
/task_list — danh sách task
/task_info <task_id> — chi tiết task
/task_edit <task_id> <tiêu đề mới> [| thời gian]
/task_done <task_id> — hoàn tất
/task_delete <task_id> — đề nghị hủy task
/today — task hôm nay
/overdue — task quá hạn
/inbox — task trong inbox
/projects — danh sách project
/remind <thời gian tiếng Việt> | <nội dung>

Ví dụ:
/task_add Gửi báo cáo | 9 giờ sáng mai
/remind 8 giờ sáng mai | Kiểm tra email""",
    """7. THIẾT LẬP
/pair <mã> — ghép chủ sở hữu lần đầu
/start — giới thiệu bot
/help — toàn bộ hướng dẫn này

Nhà cung cấp AI hỗ trợ OpenAI, OpenRouter, Ollama local hoặc tắt AI.
Chọn trực tiếp trong Telegram tại “Nhà cung cấp AI”; thay đổi áp dụng ngay.
Ollama xử lý suy luận/embedding trên máy và không cần API key.
Trong “Nhà cung cấp AI” → “Quản lý Ollama local”, owner có thể xem, tải,
xóa, chọn model chat/embedding và kích hoạt Ollama hoàn toàn bằng nút Telegram.
“Tắt toàn bộ AI” dừng OpenAI, OpenRouter, Ollama, suy luận và embedding nhưng
không dừng SQLite, tìm kiếm local, CoinGecko, đồng bộ hoặc chống spam.

QUY ƯỚC
<...> là giá trị bắt buộc; [...] là tùy chọn. Không nhập nguyên dấu < > hoặc [ ].

Ví dụ cấp quyền an toàn:
/group_allow -1001234567890
/confirm <action_id bot vừa trả>
/permission_template -1001234567890 knowledge
/confirm <action_id bot vừa trả>

Mẫu knowledge cho phép đọc, đồng bộ, tìm kiếm và tóm tắt nhưng không cấp quyền gửi hoặc xóa tin.""",
)


def group_rank_query(
    *,
    limit: int | None = None,
    offset: int = 0,
    chat_types: tuple[str, ...] = GROUP_CHAT_TYPES,
    category: str = "all",
):
    """Build a portable ranking query from locally synchronized interactions."""
    if category not in GROUP_LIST_CATEGORIES:
        raise ValueError("Danh mục group không hợp lệ")
    stats = (
        select(
            TelegramMessage.chat_id.label("chat_id"),
            func.sum(case((TelegramMessage.is_outgoing.is_(True), 1), else_=0)).label(
                "outgoing_messages"
            ),
            func.count(TelegramMessage.id).label("synced_messages"),
            func.max(TelegramMessage.sent_at).label("last_message_at"),
        )
        .where(TelegramMessage.is_deleted.is_(False))
        .group_by(TelegramMessage.chat_id)
        .subquery()
    )
    outgoing = func.coalesce(stats.c.outgoing_messages, 0).label("outgoing_messages")
    synced = func.coalesce(stats.c.synced_messages, 0).label("synced_messages")
    last_activity = func.coalesce(stats.c.last_message_at, TelegramChat.last_seen_at).label(
        "last_activity"
    )
    category_filters = []
    if category != "all":
        allowed = (
            select(TelegramChatPolicy.chat_id)
            .where(
                TelegramChatPolicy.chat_id == TelegramChat.chat_id,
                TelegramChatPolicy.allowed.is_(True),
            )
            .exists()
        )
        ai_enabled = (
            select(TelegramChatPermission.id)
            .where(
                TelegramChatPermission.chat_id == TelegramChat.chat_id,
                TelegramChatPermission.permission == PermissionName.GROUP_AI_ASK.value,
                TelegramChatPermission.enabled.is_(True),
            )
            .exists()
        )
        if category == "ai":
            category_filters.extend([allowed, ai_enabled])
        else:
            other_permission = (
                select(TelegramChatPermission.id)
                .where(
                    TelegramChatPermission.chat_id == TelegramChat.chat_id,
                    TelegramChatPermission.permission != PermissionName.GROUP_AI_ASK.value,
                    TelegramChatPermission.enabled.is_(True),
                )
                .exists()
            )
            category_filters.extend([allowed, other_permission, ~ai_enabled])
    statement = (
        select(TelegramChat, outgoing, synced, last_activity)
        .outerjoin(stats, stats.c.chat_id == TelegramChat.chat_id)
        .where(TelegramChat.chat_type.in_(chat_types), *category_filters)
        .order_by(
            outgoing.desc(),
            synced.desc(),
            last_activity.desc(),
            TelegramChat.title.asc(),
        )
    )
    if limit is not None:
        statement = statement.limit(limit)
    return statement.offset(offset) if offset else statement


def group_search_query(
    raw_query: str,
    *,
    limit: int = 10,
    chat_types: tuple[str, ...] = GROUP_CHAT_TYPES,
):
    query = raw_query.strip()
    filters = [
        folded_contains(TelegramChat.title, query),
        folded_contains(TelegramChat.username, query.removeprefix("@")),
    ]
    try:
        filters.append(TelegramChat.chat_id == int(query.replace(",", "")))
    except ValueError:
        pass
    return (
        select(TelegramChat)
        .where(
            TelegramChat.chat_type.in_(chat_types),
            or_(*filters),
        )
        .order_by(TelegramChat.title.asc())
        .limit(limit)
    )


def format_chat_summary(
    chat: TelegramChat,
    *,
    allowed: bool,
    outgoing_messages: int = 0,
    synced_messages: int = 0,
    last_activity: datetime | None = None,
) -> str:
    """Format one Telegram dialog so the human-readable name is prominent."""
    title = (chat.title or "").strip()
    username = (chat.username or "").strip()
    fallback_names = {
        "private": "Chat riêng không có tên",
        "group": "Nhóm không có tiêu đề",
        "supergroup": "Nhóm không có tiêu đề",
        "channel": "Kênh không có tiêu đề",
    }
    display_name = title or (
        f"@{username}" if username else fallback_names.get(chat.chat_type, "Không có tên")
    )
    username_line = f"\nUsername: @{username}" if username else ""
    return (
        f"Tên: {display_name}\n"
        f"ID: {chat.chat_id}\n"
        f"Loại: {chat.chat_type}\n"
        f"Trạng thái: {'ALLOW' if allowed else 'BLOCK'}\n"
        f"Tương tác: {outgoing_messages} tin đã gửi / "
        f"{synced_messages} tin đã đồng bộ\n"
        f"Hoạt động gần nhất: {last_activity or '-'}"
        f"{username_line}"
    )


def chunk_chat_summaries(entries: list[str], *, limit: int = 3800) -> list[str]:
    """Keep /groups responses below Telegram's message-size limit."""
    chunks: list[str] = []
    current = "10 nhóm tương tác nhiều nhất:\n\n"
    for entry in entries:
        addition = entry if current.endswith("\n\n") else f"\n\n{entry}"
        if len(current) + len(addition) > limit and current.strip():
            chunks.append(current.rstrip())
            current = entry
        else:
            current += addition
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


def _csv_safe(value: object) -> object:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return f"'{value}"
    return value


def build_groups_csv(
    rows: list[tuple[TelegramChat, int, int, datetime | None]],
    policies: dict[int, TelegramChatPolicy],
) -> bytes:
    """Create an Excel-friendly CSV without writing Telegram metadata to disk."""
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "Xếp hạng",
            "Tên nhóm",
            "Chat ID",
            "Loại",
            "Username",
            "Trạng thái",
            "Tin đã gửi",
            "Tin đã đồng bộ",
            "Hoạt động gần nhất",
        ]
    )
    for rank, (chat, outgoing, synced, last_activity) in enumerate(rows, start=1):
        policy = policies.get(chat.chat_id)
        writer.writerow(
            [
                rank,
                _csv_safe((chat.title or "").strip() or "Nhóm không có tiêu đề"),
                chat.chat_id,
                chat.chat_type,
                _csv_safe(f"@{chat.username}" if chat.username else ""),
                "ALLOW" if policy and policy.allowed else "BLOCK",
                int(outgoing or 0),
                int(synced or 0),
                last_activity.isoformat() if last_activity else "",
            ]
        )
    return output.getvalue().encode("utf-8-sig")


def groups_menu_keyboard(
    rows: list[tuple[TelegramChat, int, int, datetime | None]],
    *,
    page: int = 0,
    has_next: bool = False,
    category: str = "all",
) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton(
                text=f"{index}. {(chat.title or 'Nhóm không có tiêu đề')[:45]}",
                callback_data=f"group:open:{chat.chat_id}",
            )
        ]
        for index, (chat, _outgoing, _synced, _last_activity) in enumerate(rows, start=1)
    ]
    buttons.append(
        [
            InlineKeyboardButton(
                text="Tìm nhóm",
                callback_data="groups:search",
            )
        ]
    )
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        previous_callback = (
            f"groups:page:{page - 1}" if category == "all" else f"groups:page:{category}:{page - 1}"
        )
        navigation.append(
            InlineKeyboardButton(
                text="Trang trước",
                callback_data=previous_callback,
            )
        )
    if has_next:
        next_callback = (
            f"groups:page:{page + 1}" if category == "all" else f"groups:page:{category}:{page + 1}"
        )
        navigation.append(
            InlineKeyboardButton(
                text="Trang sau",
                callback_data=next_callback,
            )
        )
    if navigation:
        buttons.append(navigation)
    buttons.append(
        [
            InlineKeyboardButton(
                text="Xuất toàn bộ CSV",
                callback_data="groups:export_csv",
            ),
            InlineKeyboardButton(
                text="Quay lại quản lý nhóm",
                callback_data="groups:show",
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def groups_hub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Nhóm đã bật AI",
                    callback_data="groups:list:ai",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Nhóm có quyền khác (chưa bật AI)",
                    callback_data="groups:list:permissions",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Tất cả nhóm",
                    callback_data="groups:list:all",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Tìm nhóm",
                    callback_data="groups:search",
                ),
                InlineKeyboardButton(
                    text="Xuất toàn bộ CSV",
                    callback_data="groups:export_csv",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Đóng",
                    callback_data="groups:dismiss_export",
                )
            ],
        ]
    )


def group_actions_keyboard(
    chat_id: int,
    *,
    auto_enabled: bool = False,
    ai_ask_enabled: bool = False,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        "Tắt AUTO xóa link non-admin"
                        if auto_enabled
                        else "Bật AUTO xóa link non-admin"
                    ),
                    callback_data=(
                        f"moderation:link_auto_off:{chat_id}"
                        if auto_enabled
                        else f"moderation:link_auto_on:{chat_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text=("Tắt thành viên hỏi AI" if ai_ask_enabled else "Bật thành viên hỏi AI"),
                    callback_data=(
                        f"group_ai:ask_off:{chat_id}"
                        if ai_ask_enabled
                        else f"group_ai:ask_on:{chat_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    text="AI mode, fallback & quota",
                    callback_data=f"group_ai:policy:{chat_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Thiết lập chống spam",
                    callback_data=f"moderation:setup:{chat_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Đồng bộ 1.000 tin mới nhất",
                    callback_data=f"moderation:sync:{chat_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Quét các tin chứa link",
                    callback_data=f"moderation:scan:{chat_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Quay lại danh sách",
                    callback_data="groups:show",
                )
            ],
        ]
    )


def group_ai_policy_keyboard(chat_id: int, *, mode: str) -> InlineKeyboardMarkup:
    def label(value: str, text: str) -> str:
        return f"✅ {text}" if mode == value else text

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label("inherit", "Theo AI toàn cục"),
                    callback_data=f"group_ai:mode:{chat_id}:inherit",
                ),
                InlineKeyboardButton(
                    text=label("off", "Tắt AI group"),
                    callback_data=f"group_ai:mode:{chat_id}:off",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=label("local_only", "LOCAL ONLY"),
                    callback_data=f"group_ai:mode:{chat_id}:local_only",
                ),
                InlineKeyboardButton(
                    text=label("local_first", "Local → Cloud"),
                    callback_data=f"group_ai:mode:{chat_id}:local_first",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=label("cloud_only", "Cloud only"),
                    callback_data=f"group_ai:mode:{chat_id}:cloud_only",
                ),
                InlineKeyboardButton(
                    text=label("cloud_first", "Cloud → Local"),
                    callback_data=f"group_ai:mode:{chat_id}:cloud_first",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Cloud: OpenAI",
                    callback_data=f"group_ai:cloud:{chat_id}:openai",
                ),
                InlineKeyboardButton(
                    text="Cloud: OpenRouter",
                    callback_data=f"group_ai:cloud:{chat_id}:openrouter",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Quota nhẹ",
                    callback_data=f"group_ai:quota:{chat_id}:light",
                ),
                InlineKeyboardButton(
                    text="Quota lớn",
                    callback_data=f"group_ai:quota:{chat_id}:large",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Không giới hạn quota",
                    callback_data=f"group_ai:quota:{chat_id}:unlimited",
                ),
                InlineKeyboardButton(
                    text="Tùy chỉnh",
                    callback_data=f"group_ai:quota:{chat_id}:custom",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Retention 7 ngày",
                    callback_data=f"group_ai:retention:{chat_id}:7",
                ),
                InlineKeyboardButton(
                    text="30 ngày",
                    callback_data=f"group_ai:retention:{chat_id}:30",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Giữ mãi",
                    callback_data=f"group_ai:retention:{chat_id}:forever",
                ),
                InlineKeyboardButton(
                    text="Về quản lý group",
                    callback_data=f"group:open:{chat_id}",
                ),
            ],
        ]
    )


def action_confirmation_keyboard(action_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Xác nhận",
                    callback_data=f"action:confirm:{action_id}",
                ),
                InlineKeyboardButton(
                    text="Hủy",
                    callback_data=f"action:cancel:{action_id}",
                ),
            ]
        ]
    )


def ai_menu_keyboard(*, enabled: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Hỏi AI",
                    callback_data="ai:ask",
                ),
                InlineKeyboardButton(
                    text="Tìm trong Telegram",
                    callback_data="ai:search",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Kiểm tra giá CoinGecko",
                    callback_data="ai:price",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Tổng hợp toàn bộ hôm nay",
                    callback_data="ai:digest:today",
                ),
                InlineKeyboardButton(
                    text="Tổng hợp toàn bộ hôm qua",
                    callback_data="ai:digest:yesterday",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Học từ group/channel",
                    callback_data="ai:learn",
                ),
                InlineKeyboardButton(
                    text="Tiến độ học",
                    callback_data="ai:learn:status",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Xuất kiểm kê nguồn học",
                    callback_data="ai:learn:audit",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Vận hành & lưu trữ",
                    callback_data="ai:operations",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Trạng thái & ngân sách",
                    callback_data="ai:status",
                ),
                InlineKeyboardButton(
                    text="Tắt AI" if enabled else "Bật AI",
                    callback_data="ai:toggle:off" if enabled else "ai:toggle:on",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Hướng dẫn AI",
                    callback_data="ai:help",
                ),
                InlineKeyboardButton(
                    text="Nhà cung cấp AI",
                    callback_data="ai:provider",
                ),
            ],
        ]
    )


def ai_provider_keyboard(*, provider: str) -> InlineKeyboardMarkup:
    def label(name: str, text: str) -> str:
        return f"✅ {text}" if provider == name else text

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label("openai", "OpenAI trực tiếp"),
                    callback_data="ai:provider:set:openai",
                ),
                InlineKeyboardButton(
                    text=label("openrouter", "OpenRouter"),
                    callback_data="ai:provider:set:openrouter",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=label("ollama", "Ollama local"),
                    callback_data="ai:provider:set:ollama",
                ),
                InlineKeyboardButton(
                    text=label("off", "Tắt toàn bộ AI"),
                    callback_data="ai:provider:set:off",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Quản lý model Ollama",
                    callback_data="ollama:menu",
                )
            ],
            [InlineKeyboardButton(text="Về menu AI", callback_data="ai:menu")],
        ]
    )


def ollama_menu_keyboard(*, active: bool) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Đang dùng Ollama" if active else "Kích hoạt Ollama",
                    callback_data="ollama:activate",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Chọn model chat",
                    callback_data="ollama:choose:chat",
                ),
                InlineKeyboardButton(
                    text="Chọn embedding",
                    callback_data="ollama:choose:embedding",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Tải thêm model",
                    callback_data="ollama:pull",
                ),
                InlineKeyboardButton(
                    text="Xóa model",
                    callback_data="ollama:choose:delete",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Làm mới danh sách",
                    callback_data="ollama:menu",
                ),
                InlineKeyboardButton(
                    text="Về nhà cung cấp AI",
                    callback_data="ai:provider",
                ),
            ],
        ]
    )


def ollama_models_keyboard(
    models: list[OllamaModel],
    *,
    action: str,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{model.name[:42]} · {format_model_size(model.size)}",
                callback_data=f"ollama:{action}:set:{index}",
            )
        ]
        for index, model in enumerate(models[:30])
    ]
    rows.append([InlineKeyboardButton(text="Về quản lý Ollama", callback_data="ollama:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def ai_learning_groups_keyboard(
    rows: list[tuple],
    *,
    page: int,
    has_next: bool,
    selected_chat_ids: set[int] | None = None,
) -> InlineKeyboardMarkup:
    selected = selected_chat_ids or set()
    keyboard = [
        [
            InlineKeyboardButton(
                text=(
                    f"{'✅' if chat.chat_id in selected else '⬜'} "
                    f"{'📣' if chat.chat_type == 'channel' else '👥'} "
                    f"{index}. {(chat.title or 'Nguồn không có tiêu đề')[:38]}"
                ),
                callback_data=f"ai:learn:toggle:{chat.chat_id}:{page}",
            )
        ]
        for index, (chat, _outgoing, _synced, _last_activity) in enumerate(
            rows,
            start=page * 10 + 1,
        )
    ]
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="Trang trước",
                callback_data=f"ai:learn:page:{page - 1}",
            )
        )
    if has_next:
        navigation.append(
            InlineKeyboardButton(
                text="Trang sau",
                callback_data=f"ai:learn:page:{page + 1}",
            )
        )
    if navigation:
        keyboard.append(navigation)
    keyboard.extend(
        [
            [
                InlineKeyboardButton(
                    text="Chọn cả trang",
                    callback_data=f"ai:learn:select_page:{page}",
                ),
                InlineKeyboardButton(
                    text=f"Chọn tất cả (≤{LEARNING_SELECTION_LIMIT})",
                    callback_data="ai:learn:select_all",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Tìm nguồn",
                    callback_data="ai:learn:search",
                ),
                InlineKeyboardButton(
                    text="Bỏ chọn tất cả",
                    callback_data="ai:learn:clear",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"Tiếp tục với {len(selected)} nguồn",
                    callback_data="ai:learn:review",
                ),
            ],
            [
                InlineKeyboardButton(text="Fine-tune là gì?", callback_data="ai:learn:about"),
                InlineKeyboardButton(text="Về menu AI", callback_data="ai:menu"),
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def ai_followup_keyboard(*, mode: str) -> InlineKeyboardMarkup:
    first = InlineKeyboardButton(
        text="Hỏi tiếp" if mode == "ask" else "Tìm tiếp",
        callback_data="ai:ask" if mode == "ask" else "ai:search",
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                first,
                InlineKeyboardButton(
                    text="Về menu AI",
                    callback_data="ai:menu",
                ),
            ]
        ]
    )


def split_telegram_text(text: str, *, limit: int = 3900) -> list[str]:
    if not text:
        return [""]
    return [text[index : index + limit] for index in range(0, len(text), limit)]


INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]\n]+)]\((https?://[^\s)]+)\)")
BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
UNDERSCORE_BOLD_RE = re.compile(r"__([^_\n]+)__")
ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
STRIKE_RE = re.compile(r"~~([^~\n]+)~~")
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$")
BULLET_RE = re.compile(r"^\s*[-*+]\s+(.+)$")
NUMBERED_RE = re.compile(r"^\s*(\d+)[.)]\s+(.+)$")


def _telegram_inline_html(text: str) -> str:
    tokens: list[str] = []

    def stash(value: str) -> str:
        tokens.append(value)
        return f"\x00{len(tokens) - 1}\x00"

    protected = INLINE_CODE_RE.sub(
        lambda match: stash(f"<code>{html.escape(match.group(1))}</code>"),
        text,
    )

    def link(match: re.Match[str]) -> str:
        label = html.escape(match.group(1))
        url = html.escape(match.group(2), quote=True)
        return stash(f'<a href="{url}">{label}</a>')

    protected = MARKDOWN_LINK_RE.sub(link, protected)
    escaped = html.escape(protected)
    escaped = BOLD_RE.sub(r"<b>\1</b>", escaped)
    escaped = UNDERSCORE_BOLD_RE.sub(r"<b>\1</b>", escaped)
    escaped = ITALIC_RE.sub(r"<i>\1</i>", escaped)
    escaped = STRIKE_RE.sub(r"<s>\1</s>", escaped)
    for index, token in enumerate(tokens):
        escaped = escaped.replace(f"\x00{index}\x00", token)
    return escaped


def markdown_to_telegram_html(text: str) -> str:
    """Convert the small Markdown subset produced by AI to Telegram-safe HTML."""
    output: list[str] = []
    code_lines: list[str] = []
    in_code_block = False
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line.strip().startswith("```"):
            if in_code_block:
                output.append(f"<pre>{html.escape(chr(10).join(code_lines))}</pre>")
                code_lines = []
                in_code_block = False
            else:
                in_code_block = True
            continue
        if in_code_block:
            code_lines.append(raw_line)
            continue
        heading = HEADING_RE.match(raw_line)
        bullet = BULLET_RE.match(raw_line)
        numbered = NUMBERED_RE.match(raw_line)
        if heading:
            output.append(f"<b>{_telegram_inline_html(heading.group(1))}</b>")
        elif bullet:
            output.append(f"• {_telegram_inline_html(bullet.group(1))}")
        elif numbered:
            output.append(f"{numbered.group(1)}. {_telegram_inline_html(numbered.group(2))}")
        elif raw_line.startswith(">"):
            output.append(
                f"<blockquote>{_telegram_inline_html(raw_line.lstrip('> '))}</blockquote>"
            )
        else:
            output.append(_telegram_inline_html(raw_line))
    if in_code_block:
        output.append(f"<pre>{html.escape(chr(10).join(code_lines))}</pre>")
    return "\n".join(output)


def telegram_html_chunks(text: str, *, limit: int = 3900) -> list[str]:
    """Split on complete paragraphs/lines so Telegram HTML tags never get cut."""
    raw_blocks = [block for block in re.split(r"\n{2,}", text) if block]
    html_blocks: list[str] = []
    for raw_block in raw_blocks:
        converted = markdown_to_telegram_html(raw_block)
        if len(converted) <= limit:
            html_blocks.append(converted)
            continue
        for raw_line in raw_block.splitlines():
            converted_line = markdown_to_telegram_html(raw_line)
            if len(converted_line) <= limit:
                html_blocks.append(converted_line)
                continue
            remaining = raw_line
            while remaining:
                cut = min(len(remaining), max(1, limit - 200))
                if cut < len(remaining):
                    boundary = remaining.rfind(" ", 0, cut)
                    if boundary > 0:
                        cut = boundary
                html_blocks.append(markdown_to_telegram_html(remaining[:cut]))
                remaining = remaining[cut:].lstrip()
    if not html_blocks:
        return [""]
    chunks: list[str] = []
    current = ""
    for block in html_blocks:
        addition = block if not current else f"\n\n{block}"
        if current and len(current) + len(addition) > limit:
            chunks.append(current)
            current = block
        else:
            current += addition
    if current:
        chunks.append(current)
    return chunks


def digest_window(
    period: str,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Return the exact Vietnam-local calendar window converted to UTC."""
    vietnam = ZoneInfo("Asia/Ho_Chi_Minh")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    local_now = current.astimezone(vietnam)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "yesterday":
        start = today_start - timedelta(days=1)
        end = today_start
    else:
        start = today_start
        end = local_now
    return start.astimezone(UTC), end.astimezone(UTC)


class _ManagementUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("bot_management_unavailable")


class _ManagementUpdateGate(BaseMiddleware):
    def __init__(self, owner):
        self._owner = owner

    async def __call__(self, handler, event, data):
        allowed = (
            self._owner._owner(event)
            if isinstance(event, Message)
            else self._owner._owner_callback(event)
            if isinstance(event, CallbackQuery)
            else False
        )
        if not allowed:
            return None
        task = asyncio.create_task(handler(event, data))
        self._owner._handler_tasks.add(task)
        try:
            return await task
        except _ManagementUnavailable:
            # Admission can expire during awaited work. Do not emit a reply or
            # serialize the update/body while withdrawing that capability.
            return None
        finally:
            self._owner._handler_tasks.discard(task)


class _ManagementRequestGate(BaseRequestMiddleware):
    def __init__(self, admission):
        self._admission = admission

    async def __call__(self, make_request, bot, method):
        # Owning health measurements continue while management is withdrawn.
        # Pairing-only setup never constructs ControlBot or installs this gate.
        if not isinstance(method, (GetMe, GetUpdates)) and not self._admission():
            raise _ManagementUnavailable()
        return await make_request(bot, method)


class ControlBot:
    def __init__(
        self,
        token: str,
        *,
        owner_id: int,
        database: object,
        policy: PolicyEngine,
        pairing: PairingCode | None = None,
        ai: AiEngine | None = None,
        rag: RagService | None = None,
        budget: BudgetService | None = None,
        coingecko: CoinGeckoClient | None = None,
        ollama: OllamaService | None = None,
        ollama_activate_handler: OllamaActivateHandler | None = None,
        ai_provider_switch_handler: AiProviderSwitchHandler | None = None,
        bot_instance: Bot | None = None,
        admission: Callable[[], bool] | None = None,
        first_source_validator=None,
        polling_runner: Callable[[Dispatcher, Bot], Awaitable[None]] | None = None,
    ) -> None:
        if bot_instance is not None and not isinstance(bot_instance, Bot):
            raise _ManagementUnavailable()
        self._owns_bot, self._closing = bot_instance is None, False
        self._admission, self._polling_runner = admission, polling_runner
        self.bot = Bot(token) if self._owns_bot else bot_instance
        self.dp, self.router = Dispatcher(), Router()
        self.owner_id, self.database, self.policy, self.pairing = (
            owner_id,
            database,
            policy,
            pairing,
        )
        self.ai, self.rag, self.budget, self.coingecko = ai, rag, budget, coingecko
        if self.rag:
            self.rag.database = self.database
        self.ollama = ollama
        self.ollama_activate_handler = ollama_activate_handler
        self.ai_provider_switch_handler = ai_provider_switch_handler
        self._background_tasks: set[asyncio.Task] = set()
        self._handler_tasks: set[asyncio.Task] = set()
        self.actions, self.tasks, self.search = (
            PendingActionService(first_source_validator=first_source_validator),
            TaskService(),
            SearchService(policy),
        )
        self.memories = MemoryService()
        self._register()
        gate = _ManagementUpdateGate(self)
        self.router.message.outer_middleware(gate)
        self.router.callback_query.outer_middleware(gate)
        self._request_gate = _ManagementRequestGate(self._admitted)
        self.bot.session.middleware.register(self._request_gate)
        self.dp.include_router(self.router)

    def _admitted(self) -> bool:
        if self._closing or not callable(self._admission):
            return False
        try:
            return self._admission() is True
        except Exception:
            return False

    def _owner(self, message: Message) -> bool:
        return self._private_owner(getattr(message, "from_user", None), message)

    def _private_owner(self, sender, message) -> bool:
        chat = getattr(message, "chat", None)
        return (
            type(self.owner_id) is int
            and self.owner_id > 0
            and sender is not None
            and type(sender.id) is int
            and sender.id == self.owner_id
            and getattr(sender, "is_bot", True) is False
            and not isinstance(message, InaccessibleMessage)
            and getattr(chat, "type", None) == "private"
            and type(getattr(chat, "id", None)) is int
            and chat.id == self.owner_id
            and self._admitted()
        )

    def _owner_callback(self, callback: CallbackQuery) -> bool:
        return not getattr(callback, "inline_message_id", None) and self._private_owner(
            callback.from_user, callback.message
        )

    async def _deny(self, message: Message) -> None:
        # Không tiết lộ trạng thái hệ thống cho người lạ.
        return None

    async def _group_rows(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        category: str = "all",
    ):
        async with self.database.session() as session:
            return list(
                (
                    await session.execute(
                        group_rank_query(
                            limit=limit,
                            offset=offset,
                            category=category,
                        )
                    )
                ).all()
            )

    async def _learning_rows(self, *, limit: int | None = None, offset: int = 0):
        async with self.database.session() as session:
            return list(
                (
                    await session.execute(
                        group_rank_query(
                            limit=limit,
                            offset=offset,
                            chat_types=LEARNING_CHAT_TYPES,
                        )
                    )
                ).all()
            )

    async def _send_groups_menu(
        self,
        chat_id: int,
        *,
        page: int = 0,
        text: str | None = None,
        category: str = "all",
    ) -> None:
        if category not in GROUP_LIST_CATEGORIES:
            category = "all"
        page = max(page, 0)
        fetched = await self._group_rows(
            limit=11,
            offset=page * 10,
            category=category,
        )
        rows, has_next = fetched[:10], len(fetched) > 10
        if not rows:
            await self.bot.send_message(
                chat_id,
                (
                    "Chưa có group nào trong danh mục này."
                    if category != "all"
                    else "Chưa tìm thấy group/supergroup trong tài khoản Telegram."
                ),
                reply_markup=groups_hub_keyboard(),
            )
            return
        category_labels = {
            "ai": "NHÓM ĐÃ BẬT AI",
            "permissions": "NHÓM CÓ QUYỀN KHÁC — CHƯA BẬT AI",
            "all": "TẤT CẢ NHÓM",
        }
        await self.bot.send_message(
            chat_id,
            text
            or (
                f"{category_labels[category]}\n\n"
                f"Chọn một nhóm để mở flow quản lý — trang {page + 1}. "
                "Mỗi trang tối đa 10 nhóm:"
            ),
            reply_markup=groups_menu_keyboard(
                rows,
                page=page,
                has_next=has_next,
                category=category,
            ),
        )

    async def _send_groups_hub(self, chat_id: int) -> None:
        await self.bot.send_message(
            chat_id,
            "QUẢN LÝ NHÓM\n\n"
            "Chọn danh mục cần quản lý:\n"
            "• Nhóm đã bật AI: thành viên có thể dùng @your_assistant_username /ask.\n"
            "• Nhóm có quyền khác: đã được cấp ít nhất một quyền nhưng chưa bật AI.\n"
            "• Tất cả nhóm: danh sách group như bình thường.",
            reply_markup=groups_hub_keyboard(),
        )

    @staticmethod
    async def _ai_enabled(session) -> bool:
        setting = await session.get(AppSetting, "ai_enabled")
        return not setting or setting.value is not False

    async def _current_ai_enabled(self) -> bool:
        async with self.database.session() as session:
            return await self._ai_enabled(session)

    async def _send_ai_menu(
        self,
        chat_id: int,
        *,
        text: str | None = None,
    ) -> None:
        async with self.database.session() as session:
            enabled = await self._ai_enabled(session)
        available = bool(self.ai and self.ai.available)
        status = "đang bật" if enabled and available else "đang tắt"
        if enabled and not available:
            status = "chưa có cấu hình API"
        await self.bot.send_message(
            chat_id,
            text
            or (
                f"TRỢ LÝ AI — {status}\n\n"
                "Bạn muốn làm gì? Chọn một nút bên dưới. "
                "Bot sẽ chỉ dùng các chat đã được cấp quyền và ưu tiên dữ liệu "
                "trong 7×24 giờ gần nhất tính từ lúc hỏi."
            ),
            reply_markup=ai_menu_keyboard(enabled=enabled),
        )

    async def _send_learning_groups(
        self,
        chat_id: int,
        *,
        page: int = 0,
        selected_chat_ids: set[int] | None = None,
    ) -> None:
        page = max(page, 0)
        fetched = await self._learning_rows(limit=11, offset=page * 10)
        rows, has_next = fetched[:10], len(fetched) > 10
        if not rows:
            await self.bot.send_message(
                chat_id,
                "Chưa tìm thấy group/channel để học.",
                reply_markup=ai_menu_keyboard(enabled=await self._current_ai_enabled()),
            )
            return
        await self.bot.send_message(
            chat_id,
            "HỌC TỪ GROUP/CHANNEL — RAG\n\n"
            "Đánh dấu nhiều group hoặc channel muốn đưa vào một kho kiến thức hợp nhất. "
            "Bot chỉ bắt đầu sau khi bạn xem lại toàn bộ lựa chọn và bấm xác nhận.",
            reply_markup=ai_learning_groups_keyboard(
                rows,
                page=page,
                has_next=has_next,
                selected_chat_ids=selected_chat_ids,
            ),
        )

    async def _ollama_status_text(self) -> str:
        settings = get_settings()
        if not self.ollama:
            return "Ollama chưa được khởi tạo trong tiến trình bot."
        try:
            models = await self.ollama.list_models()
        except OllamaError as exc:
            return str(exc)
        names = {model.name for model in models}
        chat_state = "đã cài" if settings.ollama_primary_model in names else "chưa có"
        embed_state = "đã cài" if settings.ollama_embedding_model in names else "chưa có"
        preview = "\n".join(
            f"• {model.name} — {format_model_size(model.size)}" for model in models[:12]
        )
        if len(models) > 12:
            preview += f"\n• … và {len(models) - 12} model khác"
        return (
            "QUẢN LÝ OLLAMA LOCAL\n\n"
            f"Trạng thái: {'đang dùng' if self.ai and self.ai.provider == 'ollama' else 'chưa kích hoạt'}\n"
            f"Model chat: {settings.ollama_primary_model} ({chat_state})\n"
            f"Embedding: {settings.ollama_embedding_model} ({embed_state})\n"
            f"Số model phát hiện: {len(models)}\n\n"
            f"{preview or 'Chưa có model nào trong Ollama.'}\n\n"
            "Bạn có thể chọn model có sẵn, tải thêm hoặc xóa model không còn dùng."
        )

    async def _send_ollama_menu(self, chat_id: int) -> None:
        await self.bot.send_message(
            chat_id,
            await self._ollama_status_text(),
            reply_markup=ollama_menu_keyboard(
                active=bool(self.ai and self.ai.provider == "ollama")
            ),
        )

    async def _send_ollama_model_choices(
        self,
        chat_id: int,
        state: FSMContext,
        *,
        action: str,
        title: str,
    ) -> None:
        if not self.ollama:
            await self.bot.send_message(chat_id, "Ollama chưa được khởi tạo.")
            return
        try:
            models = await self.ollama.list_models()
        except OllamaError as exc:
            await self.bot.send_message(
                chat_id,
                str(exc),
                reply_markup=ollama_menu_keyboard(
                    active=bool(self.ai and self.ai.provider == "ollama")
                ),
            )
            return
        if not models:
            await self.bot.send_message(
                chat_id,
                "Chưa có model Ollama. Chọn “Tải thêm model” để tải model đầu tiên.",
                reply_markup=ollama_menu_keyboard(
                    active=bool(self.ai and self.ai.provider == "ollama")
                ),
            )
            return
        local_models = [model for model in models if model.size > 0]
        if not local_models:
            await self.bot.send_message(
                chat_id,
                "Chưa phát hiện model nào được lưu trên máy.",
                reply_markup=ollama_menu_keyboard(
                    active=bool(self.ai and self.ai.provider == "ollama")
                ),
            )
            return
        shown = local_models[:30]
        await state.update_data(ollama_model_names=[model.name for model in shown])
        await self.bot.send_message(
            chat_id,
            f"{title}\n\nChọn một model bên dưới"
            + (f". Đang hiện 30/{len(local_models)} model." if len(local_models) > 30 else "."),
            reply_markup=ollama_models_keyboard(shown, action=action),
        )

    def _run_background_task(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _pull_ollama_model(self, chat_id: int, model: str) -> None:
        if not self.ollama:
            return
        try:
            await self.ollama.pull_model(model)
            text = (
                f"ĐÃ TẢI XONG MODEL OLLAMA\n\nModel: {model}\n\n"
                "Bạn có thể mở Quản lý Ollama để chọn model này."
            )
        except OllamaError as exc:
            text = f"TẢI MODEL OLLAMA THẤT BẠI\n\nModel: {model}\nLỗi: {exc}"
        await self.bot.send_message(
            chat_id,
            text,
            reply_markup=ollama_menu_keyboard(
                active=bool(self.ai and self.ai.provider == "ollama")
            ),
        )

    async def _ai_status_text(self) -> str:
        if not self.budget:
            return (
                "AI chưa được cấu hình ngân sách.\n"
                "Tìm kiếm local vẫn có thể hoạt động nếu chat đã được cấp quyền."
            )
        async with self.database.session() as session:
            enabled = await self._ai_enabled(session)
            state = await self.budget.state(session)
        available = bool(self.ai and self.ai.available)
        local = bool(self.ai and self.ai.provider == "ollama")
        if not enabled:
            status = "đã tắt"
        elif not available:
            status = "chưa kết nối được provider"
        elif local:
            status = "đang hoạt động local"
        elif state.exhausted:
            status = "đã dừng do chạm ngân sách"
        else:
            status = "đang hoạt động"
        warning = "\nCảnh báo: đã dùng từ 80% ngân sách." if state.warning and not local else ""
        budget_text = (
            "Chi phí API Ollama: $0; dữ liệu suy luận và embedding xử lý trên máy."
            if local
            else (
                f"Hôm nay: ${state.daily_spend:.4f} / ${state.daily_limit:.2f}\n"
                f"Tháng này: ${state.monthly_spend:.4f} / ${state.monthly_limit:.2f}"
            )
        )
        return (
            f"AI: {status}\n"
            f"Nhà cung cấp: {self.ai.provider if self.ai else '-'}\n"
            f"Model: {self.ai.model if self.ai else '-'}\n"
            f"Embedding: {self.ai.embedding_model if self.ai else '-'}\n"
            f"{budget_text}"
            f"{warning}\n\n"
            "Tắt AI không làm dừng tìm kiếm local, đồng bộ hoặc chống spam."
        )

    async def _ask_ai(self, question: str) -> str:
        if price_asset := extract_price_asset(question):
            return await self._coin_price_answer(price_asset)
        if not self.rag or not self.ai or not self.ai.available:
            return (
                "AI chưa sẵn sàng. Bạn vẫn có thể chọn “Tìm trong Telegram” để tìm dữ liệu local."
            )
        async with self.database.session() as session:
            if not await self._ai_enabled(session):
                return "AI đang tắt. Bấm “Về menu AI” rồi chọn “Bật AI” để sử dụng."
            chat_ids = list(
                (
                    await session.scalars(
                        select(TelegramChatPolicy.chat_id).where(
                            TelegramChatPolicy.allowed.is_(True)
                        )
                    )
                ).all()
            )
            try:
                return await self.rag.answer(
                    session,
                    question,
                    actor_id=self.owner_id,
                    owner_id=self.owner_id,
                    chat_ids=chat_ids,
                )
            except AuthorizationRevoked:
                return ""
            except RuntimeError as exc:
                return str(exc)
            except Exception:
                return "AI tạm thời không khả dụng; tìm kiếm local vẫn hoạt động."

    async def _answer_still_authorized(self, answer: str) -> bool:
        if not answer or not self._admitted():
            return False
        try:
            async with self.database.session() as session:
                await validate_answer(session, answer)
            return self._admitted()
        except AuthorizationRevoked:
            return False

    async def _coin_price_answer(self, query: str) -> str:
        if not self.coingecko or not self.coingecko.available:
            return (
                "CoinGecko chưa được cấu hình. Chạy `tg-assistant coingecko-key` "
                "trong terminal rồi khởi động lại bot."
            )
        try:
            return format_coin_price(await self.coingecko.get_price(query))
        except CoinGeckoError as exc:
            return str(exc)
        except Exception:
            return "CoinGecko tạm thời không trả được giá; vui lòng thử lại sau."

    async def _search_telegram(self, query: str) -> str:
        async with self.database.session() as session:
            chat_ids = list(
                (
                    await session.scalars(
                        select(TelegramChatPolicy.chat_id).where(
                            TelegramChatPolicy.allowed.is_(True)
                        )
                    )
                ).all()
            )
            try:
                results = await self.search.keyword(
                    session,
                    query,
                    owner_id=self.owner_id,
                    actor_id=self.owner_id,
                    chat_ids=chat_ids,
                    limit=10,
                    default_after=rag_time_window()[0],
                )
            except ValueError as exc:
                return f"Bộ lọc chưa đúng: {exc}"
            titles: dict[int, str] = {}
            if results:
                rows = (
                    await session.scalars(
                        select(TelegramChat).where(
                            TelegramChat.chat_id.in_({result.chat_id for result in results})
                        )
                    )
                ).all()
                titles = {row.chat_id: (row.title or f"Chat {row.chat_id}") for row in rows}
        if not results:
            return (
                "Không tìm thấy đủ thông tin trong khoảng thời gian đã chọn "
                "(mặc định là 7 ngày gần nhất) từ dữ liệu Telegram đã được cấp quyền."
            )
        return "\n\n".join(
            (
                f"{index}. {titles.get(result.chat_id, f'Chat {result.chat_id}')}\n"
                f"Nguồn: {result.chat_id}/{result.message_id}"
                f"{f' • {result.sent_at}' if result.sent_at else ''}\n"
                f"{result.text[:700]}"
            )
            for index, result in enumerate(results, start=1)
        )

    async def _require_digest_current(self, epochs) -> None:
        if not self._admitted():
            raise AuthorizationRevoked("owner_pairing_required")
        async with self.database.session() as session:
            await session.run_sync(lambda sync: require_digest_authorization(sync, epochs))
        if not self._admitted():
            raise AuthorizationRevoked("owner_pairing_required")

    async def _digest_still_authorized(self, output: _AuthorizedDigest) -> bool:
        try:
            await self._require_digest_current(output.epochs)
            return True
        except PermissionError:
            return False

    async def _digest(self, period: str) -> _AuthorizedDigest | None:
        try:
            return await self._build_digest(period)
        except PermissionError:
            return None

    async def _build_digest(self, period: str) -> _AuthorizedDigest:
        since, until = digest_window(period)
        async with self.database.session() as session:
            ai_enabled = await self._ai_enabled(session)
            dataset = await load_daily_digest_dataset(
                session,
                since=since,
                until=until,
            )
        epochs = dataset.authorization_epochs
        await self._require_digest_current(epochs)
        evidence_rows: list[SourceEvidence] = []
        for item in dataset.selected_items:
            chat = TelegramChat(
                chat_id=item.chat_id,
                title=item.title,
                username=item.username,
                chat_type=item.chat_type,
            )
            evidence_rows.append(
                SourceEvidence(
                    chat_id=item.chat_id,
                    message_id=item.message_id,
                    score=1.0,
                    text=item.text[:700],
                    sent_at=item.sent_at,
                    title=item.title,
                    url=telegram_message_url(chat, item.message_id),
                    citation_priority=digest_citation_priority(item),
                )
            )
        contexts = [
            source_context(index, evidence)
            for index, evidence in enumerate(evidence_rows, start=1)
        ]
        if ai_enabled and self.ai and self.ai.available and contexts:
            try:
                async with self.database.session() as session:
                    answer = await self.ai.answer(
                        session,
                        (
                            "Tạo BẢN TIN TỔNG HỢP TOÀN NGÀY bằng tiếng Việt từ dữ liệu "
                            "đã được rà soát và khử trùng. Chỉ giữ thông tin có giá trị, "
                            "bỏ quảng cáo thuần túy và không lặp cùng một sự kiện. "
                            "Phân loại rõ theo các mục phù hợp: Diễn biến nổi bật; "
                            "Thị trường và giao dịch; Dự án/token; Vĩ mô và pháp lý; "
                            "Sàn/DeFi; Bảo mật và rủi ro; Cộng đồng/sự kiện; "
                            "Task, deadline và quyết định; Khác. "
                            "Không bắt buộc tạo mục nếu không có dữ liệu. "
                            "Mỗi thông tin phải có mã dẫn chứng [S#] ngay sau câu. "
                            "Nếu cùng một sự kiện có nguồn Evidence priority: primary_publisher "
                            "và nguồn đăng lại, chỉ nêu sự kiện một lần và bắt buộc trích "
                            "nguồn primary_publisher."
                        ),
                        contexts,
                        pre_submit=lambda: self._require_digest_current(epochs),
                    )
            except PermissionError:
                raise
            except RuntimeError as exc:
                answer = str(exc)
            except Exception:
                answer = (
                    "AI tạm thời không khả dụng; dữ liệu đã được rà soát nhưng chưa thể "
                    "phân loại tự động."
                )
            answer = prefer_primary_citations(answer, dataset.selected_items)
        elif contexts:
            answer = (
                "AI đang tắt. Dữ liệu trong ngày đã được rà soát, khử trùng và thống kê "
                "nhưng chưa thể tạo phần phân loại tự động."
            )
        else:
            answer = "Không có nội dung phù hợp trong khoảng thời gian này."
        await self._require_digest_current(epochs)
        coverage = digest_coverage_text(dataset)
        warning = (
            f"CẢNH BÁO PHẠM VI\n"
            f"Còn {dataset.missing_source_count} group/channel chưa được cấp quyền "
            "tổng hợp hoặc chưa đồng bộ nội dung. Hãy dùng “Xuất kiểm kê nguồn học”, "
            "đánh dấu CO và gửi lại file để bổ sung các nguồn này."
            if dataset.missing_source_count
            else "PHẠM VI ĐẦY ĐỦ\nTất cả group/channel đã tham gia đều được cấp quyền tổng hợp."
        )
        source_list = digest_source_list(dataset)
        referenced = referenced_item_indexes(
            answer,
            item_count=len(evidence_rows),
        )
        cited_evidence = [evidence_rows[index] for index in referenced]
        citations = (
            citation_appendix(
                cited_evidence,
                source_numbers=[index + 1 for index in referenced],
            )
            if cited_evidence
            else "DẪN CHỨNG\nKhông có tin phù hợp để dẫn chứng."
        )
        output = "\n\n".join([answer, coverage, warning, source_list, citations])
        async with self.database.session() as session:
            # Serialize policy changes only for the short persistence transaction.
            await session.execute(text("BEGIN IMMEDIATE"))

            def fence(sync, *_):
                if not self._admitted():
                    raise AuthorizationRevoked("owner_pairing_required")
                require_digest_authorization(sync, epochs)
                if not self._admitted():
                    raise AuthorizationRevoked("owner_pairing_required")

            await session.run_sync(fence)
            event.listen(session.sync_session, "before_commit", fence)
            event.listen(session.sync_session, "after_flush_postexec", fence)
            session.add(
                Summary(
                    scope_type="daily_digest",
                    scope_id=since.astimezone(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat(),
                    content=output,
                    sources=[
                        {
                            "source_number": index,
                            "chat_id": evidence.chat_id,
                            "message_id": evidence.message_id,
                            "title": evidence.title,
                            "url": evidence.url,
                        }
                        for index, evidence in enumerate(evidence_rows, start=1)
                    ],
                    period_start=since,
                    period_end=until,
                )
            )
        return _AuthorizedDigest(output, epochs)

    def _register(self) -> None:
        @self.router.message(Command("pair"))
        async def pair(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            # Legacy in-memory PairingCode cannot publish durable authority.
            # Native enrollment's restricted client owns the actual pair flow.
            await message.answer(
                "Mở cửa sổ ghép bot trong ứng dụng Windows để quản lý liên kết ghép."
            )

        @self.router.message(Command("start", "help"))
        async def help_command(message: Message) -> None:
            if not self._owner(message):
                return await self._deny(message)
            for index, page in enumerate(HELP_PAGES):
                reply_markup = None
                if index == len(HELP_PAGES) - 1:
                    reply_markup = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="Mở quản lý nhóm",
                                    callback_data="groups:show",
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    text="Mở trợ lý AI",
                                    callback_data="ai:menu",
                                )
                            ],
                        ]
                    )
                await message.answer(page, reply_markup=reply_markup)

        @self.router.callback_query(F.data == "ai:menu")
        async def ai_menu(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.clear()
            await callback.answer()
            await self._send_ai_menu(callback.from_user.id)

        @self.router.callback_query(F.data == "ai:ask")
        async def ai_ask_prompt(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.set_state(AiFlowState.waiting_question)
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                "Hãy gửi câu hỏi tự nhiên. AI sẽ trả lời dựa trên dữ liệu Telegram "
                "đã được cấp quyền; nếu nguồn không đủ, bot sẽ nói rõ.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="Về menu AI", callback_data="ai:menu")]
                    ]
                ),
            )

        @self.router.message(AiFlowState.waiting_question)
        async def ai_question(message: Message, state: FSMContext) -> None:
            if not self._owner(message):
                return await self._deny(message)
            question = (message.text or "").strip()
            if not question:
                await message.answer("Hãy gửi câu hỏi bằng chữ.")
                return
            if contains_secret(question):
                await message.answer(
                    "Nội dung có vẻ chứa secret nên bot không gửi nó tới AI. "
                    "Hãy thu hồi secret nếu cần rồi gửi lại câu hỏi không chứa thông tin nhạy cảm."
                )
                return
            await state.clear()
            await self.bot.send_chat_action(message.chat.id, "typing")
            answer = await self._ask_ai(question)
            chunks = telegram_html_chunks(answer)
            for index, chunk in enumerate(chunks):
                if not await self._answer_still_authorized(answer):
                    return
                await message.answer(
                    chunk,
                    parse_mode="HTML",
                    reply_markup=(
                        ai_followup_keyboard(mode="ask") if index == len(chunks) - 1 else None
                    ),
                )

        @self.router.callback_query(F.data == "ai:price")
        async def ai_price_prompt(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.set_state(AiFlowState.waiting_price)
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                "Gửi mã hoặc tên coin cần kiểm tra, ví dụ: BTC, ETH, SOL hoặc bitcoin.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="Về menu AI", callback_data="ai:menu")]
                    ]
                ),
            )

        @self.router.message(AiFlowState.waiting_price)
        async def ai_price_query(message: Message, state: FSMContext) -> None:
            if not self._owner(message):
                return await self._deny(message)
            query = (message.text or "").strip()
            if not query:
                await message.answer("Hãy gửi mã hoặc tên coin cần kiểm tra.")
                return
            await state.clear()
            await self.bot.send_chat_action(message.chat.id, "typing")
            answer = await self._coin_price_answer(query)
            await message.answer(
                telegram_html_chunks(answer)[0],
                parse_mode="HTML",
                reply_markup=ai_menu_keyboard(enabled=await self._current_ai_enabled()),
            )

        @self.router.callback_query(F.data == "ai:search")
        async def ai_search_prompt(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.set_state(AiFlowState.waiting_search)
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                "Gửi từ khóa bạn muốn tìm. Có thể dùng bộ lọc như "
                "in:<chat_id>, after:2026-07-01 hoặc has:link. "
                "Tìm kiếm này chạy local và không tốn AI.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="Về menu AI", callback_data="ai:menu")]
                    ]
                ),
            )

        @self.router.message(AiFlowState.waiting_search)
        async def ai_search_query(message: Message, state: FSMContext) -> None:
            if not self._owner(message):
                return await self._deny(message)
            query = (message.text or "").strip()
            if not query:
                await message.answer("Hãy gửi từ khóa cần tìm bằng chữ.")
                return
            if contains_secret(query):
                await message.answer(
                    "Nội dung có vẻ chứa secret nên bot không dùng nó làm từ khóa tìm kiếm."
                )
                return
            await state.clear()
            answer = await self._search_telegram(query)
            chunks = split_telegram_text(answer)
            for index, chunk in enumerate(chunks):
                await message.answer(
                    chunk,
                    reply_markup=(
                        ai_followup_keyboard(mode="search") if index == len(chunks) - 1 else None
                    ),
                )

        @self.router.callback_query(F.data == "ai:learn")
        async def ai_learning_menu(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.set_state(AiFlowState.selecting_learning_groups)
            await state.update_data(learning_chat_ids=[])
            await callback.answer()
            await self._send_learning_groups(
                callback.from_user.id,
                selected_chat_ids=set(),
            )

        @self.router.callback_query(F.data.startswith("ai:learn:page:"))
        async def ai_learning_page(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            try:
                page = int((callback.data or "").rsplit(":", 1)[1])
            except (ValueError, IndexError):
                await callback.answer("Trang không hợp lệ.", show_alert=True)
                return
            data = await state.get_data()
            selected = {int(value) for value in data.get("learning_chat_ids", [])}
            await state.set_state(AiFlowState.selecting_learning_groups)
            await callback.answer()
            await self._send_learning_groups(
                callback.from_user.id,
                page=page,
                selected_chat_ids=selected,
            )

        @self.router.callback_query(F.data.startswith("ai:learn:toggle:"))
        async def ai_learning_toggle(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            try:
                parts = (callback.data or "").split(":")
                chat_id, page = int(parts[-2]), int(parts[-1])
            except (ValueError, IndexError):
                await callback.answer("Dữ liệu nguồn không hợp lệ.", show_alert=True)
                return
            data = await state.get_data()
            selected = {int(value) for value in data.get("learning_chat_ids", [])}
            if chat_id in selected:
                selected.remove(chat_id)
            elif len(selected) >= LEARNING_SELECTION_LIMIT:
                await callback.answer(
                    f"Mỗi đợt chọn tối đa {LEARNING_SELECTION_LIMIT} nguồn.",
                    show_alert=True,
                )
                return
            else:
                selected.add(chat_id)
            await state.set_state(AiFlowState.selecting_learning_groups)
            await state.update_data(learning_chat_ids=sorted(selected))
            await callback.answer(f"Đã chọn {len(selected)} nguồn.")
            await self._send_learning_groups(
                callback.from_user.id,
                page=page,
                selected_chat_ids=selected,
            )

        @self.router.callback_query(F.data.startswith("ai:learn:select_page:"))
        async def ai_learning_select_page(
            callback: CallbackQuery,
            state: FSMContext,
        ) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            try:
                page = int((callback.data or "").rsplit(":", 1)[1])
            except (ValueError, IndexError):
                await callback.answer("Trang không hợp lệ.", show_alert=True)
                return
            data = await state.get_data()
            selected = {int(value) for value in data.get("learning_chat_ids", [])}
            rows = await self._learning_rows(limit=10, offset=max(page, 0) * 10)
            selected.update(chat.chat_id for chat, *_stats in rows)
            if len(selected) > LEARNING_SELECTION_LIMIT:
                selected = set(sorted(selected)[:LEARNING_SELECTION_LIMIT])
            await state.set_state(AiFlowState.selecting_learning_groups)
            await state.update_data(learning_chat_ids=sorted(selected))
            await callback.answer(f"Đã chọn {len(selected)} nguồn.")
            await self._send_learning_groups(
                callback.from_user.id,
                page=page,
                selected_chat_ids=selected,
            )

        @self.router.callback_query(F.data == "ai:learn:select_all")
        async def ai_learning_select_all(
            callback: CallbackQuery,
            state: FSMContext,
        ) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            rows = await self._learning_rows(limit=LEARNING_SELECTION_LIMIT)
            selected = {chat.chat_id for chat, *_stats in rows}
            await state.set_state(AiFlowState.selecting_learning_groups)
            await state.update_data(learning_chat_ids=sorted(selected))
            await callback.answer(f"Đã chọn {len(selected)} nguồn.")
            await self._send_learning_groups(
                callback.from_user.id,
                selected_chat_ids=selected,
            )

        @self.router.callback_query(F.data == "ai:learn:clear")
        async def ai_learning_clear(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.set_state(AiFlowState.selecting_learning_groups)
            await state.update_data(learning_chat_ids=[])
            await callback.answer("Đã bỏ chọn tất cả.")
            await self._send_learning_groups(
                callback.from_user.id,
                selected_chat_ids=set(),
            )

        @self.router.callback_query(F.data == "ai:learn:search")
        async def ai_learning_search_prompt(
            callback: CallbackQuery,
            state: FSMContext,
        ) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.set_state(AiFlowState.waiting_learning_search)
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                "Gửi một phần tên group/channel, @username hoặc Chat ID.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Quay lại",
                                callback_data="ai:learn:page:0",
                            )
                        ]
                    ]
                ),
            )

        @self.router.message(AiFlowState.waiting_learning_search)
        async def ai_learning_search_query(
            message: Message,
            state: FSMContext,
        ) -> None:
            if not self._owner(message):
                return await self._deny(message)
            query = (message.text or "").strip()
            if not query:
                await message.answer("Hãy nhập tên, @username hoặc Chat ID.")
                return
            async with self.database.session() as session:
                rows = list(
                    (
                        await session.scalars(
                            group_search_query(
                                query,
                                limit=10,
                                chat_types=LEARNING_CHAT_TYPES,
                            )
                        )
                    ).all()
                )
            await state.set_state(AiFlowState.selecting_learning_groups)
            if not rows:
                await message.answer(
                    "Không tìm thấy group/channel phù hợp.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="Tìm lại",
                                    callback_data="ai:learn:search",
                                ),
                                InlineKeyboardButton(
                                    text="Quay lại",
                                    callback_data="ai:learn:page:0",
                                ),
                            ]
                        ]
                    ),
                )
                return
            await message.answer(
                "Đánh dấu thêm group/channel muốn đưa vào kho kiến thức:",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=(chat.title or f"Nguồn {chat.chat_id}")[:45],
                                callback_data=f"ai:learn:toggle_search:{chat.chat_id}",
                            )
                        ]
                        for chat in rows
                    ]
                    + [
                        [
                            InlineKeyboardButton(
                                text="Quay lại",
                                callback_data="ai:learn:page:0",
                            )
                        ]
                    ]
                ),
            )

        @self.router.callback_query(F.data.startswith("ai:learn:toggle_search:"))
        async def ai_learning_toggle_search(
            callback: CallbackQuery,
            state: FSMContext,
        ) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            try:
                chat_id = int((callback.data or "").rsplit(":", 1)[1])
            except (ValueError, IndexError):
                await callback.answer("Chat ID không hợp lệ.", show_alert=True)
                return
            data = await state.get_data()
            selected = {int(value) for value in data.get("learning_chat_ids", [])}
            if chat_id not in selected and len(selected) >= LEARNING_SELECTION_LIMIT:
                await callback.answer(
                    f"Mỗi đợt chọn tối đa {LEARNING_SELECTION_LIMIT} nguồn.",
                    show_alert=True,
                )
                return
            if chat_id in selected:
                selected.remove(chat_id)
            else:
                selected.add(chat_id)
            await state.set_state(AiFlowState.selecting_learning_groups)
            await state.update_data(learning_chat_ids=sorted(selected))
            await callback.answer(f"Đã chọn {len(selected)} nguồn.")
            await self.bot.send_message(
                callback.from_user.id,
                f"Đã chọn {len(selected)} nguồn. Bạn có thể tìm thêm, quay lại danh sách "
                "hoặc xem lại để xác nhận.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(text="Tìm thêm", callback_data="ai:learn:search"),
                            InlineKeyboardButton(
                                text="Danh sách nguồn", callback_data="ai:learn:page:0"
                            ),
                        ],
                        [
                            InlineKeyboardButton(
                                text=f"Tiếp tục với {len(selected)} nguồn",
                                callback_data="ai:learn:review",
                            )
                        ],
                    ]
                ),
            )

        @self.router.callback_query(F.data == "ai:learn:review")
        async def ai_learning_review(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            data = await state.get_data()
            selected = {int(value) for value in data.get("learning_chat_ids", [])}
            if not selected:
                await callback.answer("Bạn chưa chọn group/channel nào.", show_alert=True)
                return
            async with self.database.session() as session:
                chats = (
                    await session.scalars(
                        select(TelegramChat)
                        .where(
                            TelegramChat.chat_id.in_(selected),
                            TelegramChat.chat_type.in_(LEARNING_CHAT_TYPES),
                        )
                        .order_by(TelegramChat.title.asc())
                    )
                ).all()
            valid_ids = [chat.chat_id for chat in chats]
            if not valid_ids:
                await callback.answer("Không còn group/channel hợp lệ.", show_alert=True)
                return
            await state.set_state(AiFlowState.selecting_learning_groups)
            await state.update_data(learning_chat_ids=valid_ids)
            shown = "\n".join(f"• {chat.title or chat.chat_id}" for chat in chats[:20])
            extra = f"\n• … và {len(chats) - 20} group/channel khác" if len(chats) > 20 else ""
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                f"XEM TRƯỚC HỌC TỪ NHIỀU GROUP/CHANNEL\n\n"
                f"Đã chọn: {len(chats)} nguồn\n{shown}{extra}\n\n"
                f"Mỗi group/channel sẽ được xếp vào hàng đợi riêng, bật allowlist + quyền knowledge, "
                f"đồng bộ tối đa 1.000 tin mới nhất vào SQLite và commit trước. Sau đó worker "
                f"đọc lại từ SQLite để tạo embedding. Secret được bỏ qua; phần chữ hợp lệ "
                f"dùng để tạo embedding sẽ được gửi tới provider AI đang chọn; "
                f"nếu dùng Ollama thì phần này được xử lý hoàn toàn trên máy. "
                f"Tất cả vector được upsert vào cùng một kho Qdrant trên máy; học tiếp chỉ "
                f"bổ sung tin mới sau checkpoint.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=f"Xác nhận {len(chats)} nguồn",
                                callback_data="ai:learn:start_bulk",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="Quay lại chọn nguồn",
                                callback_data="ai:learn:page:0",
                            )
                        ],
                    ]
                ),
            )

        @self.router.callback_query(F.data == "ai:learn:start_bulk")
        async def ai_learning_start_bulk(
            callback: CallbackQuery,
            state: FSMContext,
        ) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            data = await state.get_data()
            selected = {int(value) for value in data.get("learning_chat_ids", [])}
            if not selected:
                await callback.answer("Bạn chưa chọn group/channel nào.", show_alert=True)
                return
            async with self.database.session() as session:
                valid_ids = list(
                    (
                        await session.scalars(
                            select(TelegramChat.chat_id).where(
                                TelegramChat.chat_id.in_(selected),
                                TelegramChat.chat_type.in_(LEARNING_CHAT_TYPES),
                            )
                        )
                    ).all()
                )
                if not valid_ids:
                    await callback.answer("Không còn group/channel hợp lệ.", show_alert=True)
                    return
                action = await self.actions.create(
                    session,
                    action_type="enable_group_learning_bulk",
                    requested_by=self.owner_id,
                    payload={
                        "chat_ids": valid_ids,
                        "limit": 1000,
                        "embedding": True,
                    },
                    preview=(
                        f"Học từ {len(valid_ids)} group/channel đã chọn\n"
                        f"Tối đa {len(valid_ids) * 1000:,} tin trong đợt đồng bộ đầu\n"
                        "Xử lý từng nguồn bằng hàng đợi nền\n"
                        "Bật allowlist + quyền knowledge cho từng nguồn\n"
                        "Ghi và commit dữ liệu nguồn vào SQLite trước khi tạo embedding\n"
                        "Upsert tất cả nguồn vào một kho Qdrant hợp nhất; học tiếp chỉ bổ sung tin mới\n"
                        "Nội dung hợp lệ dùng để tạo embedding sẽ được gửi tới provider đang chọn; "
                        "với Ollama, dữ liệu được xử lý trên máy."
                    ),
                    reason="Owner chọn học nhiều group/channel trong flow AI",
                )
            await state.clear()
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                f"XÁC NHẬN PHẠM VI DỮ LIỆU\n\n{action.preview}\n\n"
                "Hàng đợi chỉ được tạo khi bạn bấm Xác nhận.",
                reply_markup=action_confirmation_keyboard(action.action_id),
            )

        @self.router.callback_query(F.data == "ai:learn:status")
        async def ai_learning_status(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.clear()
            async with self.database.session() as session:
                action = await session.scalar(
                    select(PendingAction)
                    .where(
                        PendingAction.action_type.in_(
                            [
                                "enable_group_learning_bulk",
                                "enable_group_learning",
                            ]
                        )
                    )
                    .order_by(PendingAction.created_at.desc())
                    .limit(1)
                )
                all_jobs = (
                    await session.scalars(
                        select(BackgroundJob)
                        .where(BackgroundJob.job_type == "learn_group")
                        .order_by(BackgroundJob.created_at.desc())
                        .limit(500)
                    )
                ).all()
                jobs = (
                    [
                        job
                        for job in all_jobs
                        if (job.payload or {}).get("batch_action_id") == action.action_id
                    ]
                    if action and action.action_type == "enable_group_learning_bulk"
                    else []
                )
                chat_ids = {
                    int((job.payload or {}).get("chat_id"))
                    for job in jobs
                    if (job.payload or {}).get("chat_id") is not None
                }
                chats = (
                    (
                        await session.scalars(
                            select(TelegramChat).where(TelegramChat.chat_id.in_(chat_ids))
                        )
                    ).all()
                    if chat_ids
                    else []
                )
                titles = {chat.chat_id: chat.title or str(chat.chat_id) for chat in chats}
            await callback.answer()
            if not action:
                text = "Chưa có group/channel nào được đưa vào flow học."
            else:
                status_labels = {
                    "pending": "chờ xác nhận",
                    "confirmed": "đang tạo hàng đợi",
                    "executed": "đã tạo hàng đợi",
                    "failed": "lỗi",
                    "cancelled": "đã hủy",
                    "expired": "hết hạn",
                    "queued": "đang chờ",
                    "running": "đang học",
                    "paused": "tạm dừng",
                    "pause_requested": "đang dừng an toàn",
                    "completed": "hoàn tất",
                }
                counts = {
                    status: sum(job.status == status for job in jobs)
                    for status in (
                        "queued",
                        "running",
                        "paused",
                        "pause_requested",
                        "completed",
                        "failed",
                    )
                }
                synced = sum(int((job.payload or {}).get("synced_count", 0)) for job in jobs)
                mysql_messages = sum(
                    int((job.payload or {}).get("mysql_message_count", 0)) for job in jobs
                )
                indexed = sum(int((job.payload or {}).get("indexed_count", 0)) for job in jobs)
                selected_count = len((action.payload or {}).get("chat_ids", [])) or (
                    1 if action.chat_id is not None else 0
                )
                text = (
                    "TIẾN ĐỘ HỌC NHIỀU GROUP/CHANNEL\n\n"
                    f"Đợt gần nhất: {selected_count} nguồn\n"
                    f"Action: {status_labels.get(action.status, action.status)}\n"
                    f"Hoàn tất: {counts['completed']} • Đang học: {counts['running']}\n"
                    f"Đang chờ: {counts['queued']} • Lỗi: {counts['failed']}\n"
                    f"Tạm dừng: {counts['paused']} • "
                    f"Đang dừng an toàn: {counts['pause_requested']}\n"
                    f"Tin mới đồng bộ SQLite: {synced}\n"
                    f"Bản ghi SQLite khả dụng: {mysql_messages}\n"
                    f"Tổng embedding: {indexed} tin"
                )
                if jobs:
                    recent = "\n".join(
                        (
                            f"• {titles.get(int((job.payload or {}).get('chat_id')), str((job.payload or {}).get('chat_id')))}: "
                            f"{status_labels.get(job.status, job.status)}"
                        )
                        for job in jobs[:10]
                    )
                    text += f"\n\nNguồn gần đây:\n{recent}"
                if action.error:
                    text += f"\n\nLỗi action: {action.error[:300]}"
            await self.bot.send_message(
                callback.from_user.id,
                text,
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Học thêm nguồn",
                                callback_data="ai:learn",
                            ),
                            InlineKeyboardButton(
                                text="Làm mới",
                                callback_data="ai:learn:status",
                            ),
                        ],
                        [
                            InlineKeyboardButton(
                                text="Tạm dừng học",
                                callback_data="ai:learn:pause",
                            ),
                            InlineKeyboardButton(
                                text="Tiếp tục học",
                                callback_data="ai:learn:resume",
                            ),
                        ],
                        [InlineKeyboardButton(text="Về menu AI", callback_data="ai:menu")],
                    ]
                ),
            )

        @self.router.callback_query(F.data == "ai:learn:pause")
        async def ai_learning_pause(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            from ..runtime import pause_learning_jobs

            async with self.database.session() as session:
                changed = await pause_learning_jobs(session)
            await callback.answer(f"Đã gửi yêu cầu dừng {changed} job.")
            await self.bot.send_message(
                callback.from_user.id,
                f"Đã tạm dừng/yêu cầu dừng an toàn {changed} learning job.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Xem tiến độ",
                                callback_data="ai:learn:status",
                            )
                        ]
                    ]
                ),
            )

        @self.router.callback_query(F.data == "ai:learn:resume")
        async def ai_learning_resume(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            from ..runtime import resume_learning_jobs

            async with self.database.session() as session:
                changed = await resume_learning_jobs(session)
            await callback.answer(f"Đã đưa lại hàng đợi {changed} job.")
            await self.bot.send_message(
                callback.from_user.id,
                f"Đã tiếp tục {changed} learning job.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Xem tiến độ",
                                callback_data="ai:learn:status",
                            )
                        ]
                    ]
                ),
            )

        @self.router.callback_query(F.data == "ai:learn:audit")
        async def ai_learning_audit(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            async with self.database.session() as session:
                rows = await reconcile_knowledge_sources(session)
                counts = inventory_counts(rows)
            content = build_learning_inventory_csv(rows)
            filename = f"kiem-ke-nguon-hoc-{datetime.now().strftime('%Y%m%d-%H%M')}.csv"
            await state.set_state(AiFlowState.waiting_learning_audit_file)
            await callback.answer("Đã tạo file kiểm kê.")
            await self.bot.send_document(
                chat_id=callback.from_user.id,
                document=BufferedInputFile(content, filename=filename),
                caption=(
                    f"Kiểm kê {len(rows)} nguồn: "
                    f"đã học {counts['learned'] + counts['learned_warning']}, "
                    f"chưa học {counts['not_learned']}, "
                    f"không lấy được nội dung {counts['no_content']}, "
                    f"đang chờ/đang học {counts['queued'] + counts['running']}, "
                    f"lỗi {counts['failed']}."
                ),
            )
            await self.bot.send_message(
                callback.from_user.id,
                "Hãy mở file CSV, chỉ sửa hai cột:\n"
                "• can_hoc: điền CO cho nguồn cần học hoặc học lại; để trống/KHONG nếu bỏ qua.\n"
                "• ghi_chu: ghi yêu cầu hoặc lưu ý của bạn.\n\n"
                "Sau đó gửi lại chính file CSV vào đây. Bot sẽ kiểm tra file, lưu ghi chú "
                "vào SQLite và tạo bản xem trước để bạn xác nhận trước khi học.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Hủy chờ file",
                                callback_data="ai:menu",
                            )
                        ]
                    ]
                ),
            )

        @self.router.message(AiFlowState.waiting_learning_audit_file, F.document)
        async def ai_learning_audit_upload(message: Message, state: FSMContext) -> None:
            if not self._owner(message):
                return await self._deny(message)
            document = message.document
            file_name = (document.file_name or "").lower()
            if not file_name.endswith(".csv"):
                await message.answer("File cần có định dạng .csv.")
                return
            if int(document.file_size or 0) > 5 * 1024 * 1024:
                await message.answer("File CSV vượt quá giới hạn 5 MB.")
                return
            buffer = io.BytesIO()
            await self.bot.download(document, destination=buffer)
            try:
                edits = parse_learning_inventory_csv(buffer.getvalue())
            except ValueError as exc:
                await message.answer(f"Không đọc được file kiểm kê: {exc}")
                return
            secret_rows = [edit.chat_id for edit in edits if contains_secret(edit.note)]
            if secret_rows:
                await message.answer(
                    "Ghi chú có nội dung giống secret/API key. Bot không lưu hoặc xử lý file này. "
                    "Hãy xóa secret rồi gửi lại."
                )
                return
            requested_ids = [edit.chat_id for edit in edits if edit.should_learn]
            if len(requested_ids) > MAX_BULK_LEARNING_SOURCES:
                await message.answer(
                    f"Mỗi đợt chỉ nhận tối đa {MAX_BULK_LEARNING_SOURCES} nguồn cần học."
                )
                return
            edit_by_chat = {edit.chat_id: edit for edit in edits}
            async with self.database.session() as session:
                valid_ids = set(
                    (
                        await session.scalars(
                            select(TelegramChat.chat_id).where(
                                TelegramChat.chat_id.in_(list(edit_by_chat)),
                                TelegramChat.chat_type.in_(LEARNING_CHAT_TYPES),
                            )
                        )
                    ).all()
                )
                sources = {
                    source.chat_id: source
                    for source in (
                        await session.scalars(
                            select(KnowledgeSource).where(KnowledgeSource.chat_id.in_(valid_ids))
                        )
                    ).all()
                }
                for chat_id in valid_ids:
                    edit = edit_by_chat[chat_id]
                    source = sources.get(chat_id)
                    if not source:
                        source = KnowledgeSource(chat_id=chat_id)
                        session.add(source)
                    source.owner_note = edit.note or None
                    source.requested_for_learning = edit.should_learn
                selected = [chat_id for chat_id in requested_ids if chat_id in valid_ids]
                if selected:
                    notes = {
                        str(chat_id): edit_by_chat[chat_id].note
                        for chat_id in selected
                        if edit_by_chat[chat_id].note
                    }
                    note_lines = [
                        f"• {chat_id}: {notes[str(chat_id)][:160]}"
                        for chat_id in selected
                        if str(chat_id) in notes
                    ][:10]
                    action = await self.actions.create(
                        session,
                        action_type="enable_group_learning_bulk",
                        requested_by=self.owner_id,
                        payload={
                            "chat_ids": selected,
                            "limit": 1000,
                            "embedding": True,
                            "source_notes": notes,
                            "source": "learning_inventory_csv",
                        },
                        preview=(
                            f"Học hoặc cập nhật {len(selected)} nguồn từ file kiểm kê\n"
                            "Bổ sung dữ liệu mới vào kho kiến thức hợp nhất\n"
                            f"Ghi chú đã lưu: {len(notes)}"
                            + (f"\n\n{chr(10).join(note_lines)}" if note_lines else "")
                        ),
                        reason="Owner gửi lại CSV kiểm kê và đánh dấu nguồn cần học",
                    )
                else:
                    action = None
            await state.clear()
            invalid_count = len(edit_by_chat) - len(valid_ids)
            if not action:
                await message.answer(
                    f"Đã lưu ghi chú cho {len(valid_ids)} nguồn vào SQLite. "
                    "Không có nguồn nào được đánh dấu CO nên chưa tạo hàng đợi học."
                )
                return
            await message.answer(
                f"Đã đọc {len(edits)} dòng, chọn {len(selected)} nguồn cần học"
                f"{f'; bỏ qua {invalid_count} Chat ID không hợp lệ' if invalid_count else ''}.\n\n"
                f"{action.preview}\n\nBấm Xác nhận để tạo hàng đợi.",
                reply_markup=action_confirmation_keyboard(action.action_id),
            )

        @self.router.message(AiFlowState.waiting_learning_audit_file)
        async def ai_learning_audit_waiting(message: Message) -> None:
            if not self._owner(message):
                return await self._deny(message)
            await message.answer(
                "Mình đang chờ file CSV kiểm kê. Hãy gửi file dưới dạng tài liệu Telegram."
            )

        @self.router.callback_query(F.data == "ai:learn:about")
        async def ai_learning_about(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                "FINE-TUNE VÀ HỌC TỪ GROUP/CHANNEL\n\n"
                "Fine-tune dùng bộ ví dụ đầu vào/đầu ra để thay đổi hành vi của một model "
                f"được hỗ trợ. Model hiện tại "
                f"{self.ai.model if self.ai else 'chưa cấu hình'} không hỗ trợ fine-tune.\n\n"
                "Với dữ liệu group/channel thường xuyên thay đổi, RAG phù hợp hơn: lập chỉ mục "
                "tất cả nguồn trong một kho hợp nhất, tìm đúng đoạn liên quan lúc bạn hỏi và "
                "giữ nguồn chat/message. Học tiếp sẽ bổ sung tin mới vào chính kho này. "
                "Bạn có thể block group để dừng sử dụng dữ liệu của group đó.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Quay lại",
                                callback_data="ai:learn:page:0",
                            )
                        ]
                    ]
                ),
            )

        @self.router.callback_query(F.data.startswith("ai:digest:"))
        async def ai_digest(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.clear()
            period = (callback.data or "").rsplit(":", 1)[-1]
            period = period if period in {"today", "yesterday"} else "today"
            await callback.answer("Đang tạo bản tóm tắt…")
            await self.bot.send_chat_action(callback.from_user.id, "typing")
            output = await self._digest(period)
            if output is None:
                return
            label = "HÔM NAY" if period == "today" else "HÔM QUA"
            chunks = telegram_html_chunks(f"## TỔNG HỢP {label}\n\n{output.text}")
            for index, chunk in enumerate(chunks):
                markup = (
                    ai_menu_keyboard(enabled=await self._current_ai_enabled())
                    if index == len(chunks) - 1 else None
                )
                if not await self._digest_still_authorized(output):
                    return
                await self.bot.send_message(
                    callback.from_user.id,
                    chunk,
                    parse_mode="HTML",
                    reply_markup=markup,
                )

        @self.router.callback_query(F.data == "ai:status")
        async def ai_status_callback(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.clear()
            await callback.answer()
            enabled = await self._current_ai_enabled()
            await self.bot.send_message(
                callback.from_user.id,
                await self._ai_status_text(),
                reply_markup=ai_menu_keyboard(enabled=enabled),
            )

        @self.router.callback_query(F.data == "ai:operations")
        async def ai_operations(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.clear()
            settings = get_settings()
            async with self.database.session() as session:
                current = await collect_runtime_metric(
                    session,
                    data_root=settings.data_dir,
                    vector_root=settings.resolved_semantic_vector_path,
                    media_root=settings.data_dir / "downloads",
                )
                previous = await session.scalar(
                    select(RuntimeMetric)
                    .where(RuntimeMetric.id != current.id)
                    .order_by(RuntimeMetric.collected_at.desc())
                    .limit(1)
                )
            growth = (current.data_bytes or 0) - (previous.data_bytes or 0) if previous else 0
            child_lines = "\n".join(
                f"• {row.get('name', '?')} PID {row.get('pid')}: "
                f"RAM {format_bytes(row.get('rss_bytes'))} • CPU {row.get('cpu_percent', 0):.1f}%"
                for row in (current.details or {}).get("children", [])[:10]
            )
            text = (
                "VẬN HÀNH & LƯU TRỮ\n\n"
                f"Tiến trình chính PID {current.process_id}\n"
                f"RAM: {format_bytes(current.rss_bytes)} • "
                f"CPU: {current.cpu_percent or 0:.1f}% • "
                f"VRAM: {format_bytes(current.vram_bytes)}\n"
                f"Dữ liệu: {format_bytes(current.data_bytes)} "
                f"(tăng {format_bytes(growth)})\n"
                f"Vector: {format_bytes(current.vector_bytes)} • "
                f"Media: {format_bytes(current.media_bytes)}\n"
                f"Job: {current.queued_jobs} chờ • {current.running_jobs} chạy • "
                f"{current.paused_jobs} tạm dừng\n"
            )
            if child_lines:
                text += f"\nTiến trình con:\n{child_lines}"
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                text,
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Làm mới",
                                callback_data="ai:operations",
                            ),
                            InlineKeyboardButton(
                                text="Xem trước cleanup",
                                callback_data="ai:cleanup:preview",
                            ),
                        ],
                        [
                            InlineKeyboardButton(
                                text="Tiến độ học",
                                callback_data="ai:learn:status",
                            ),
                            InlineKeyboardButton(
                                text="Về menu AI",
                                callback_data="ai:menu",
                            ),
                        ],
                    ]
                ),
            )

        @self.router.callback_query(F.data == "ai:cleanup:preview")
        async def ai_cleanup_preview(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            settings = get_settings()
            async with self.database.session() as session:
                report = await cleanup_storage(
                    session,
                    self.rag.vectors if self.rag else None,
                    media_root=settings.data_dir / "downloads",
                    dry_run=True,
                )
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                (
                    "XEM TRƯỚC STORAGE CLEANUP\n\n"
                    f"Message hết retention/vượt quota: {report.expired_messages}\n"
                    f"Vector đi cùng message: {report.expired_vectors}\n"
                    f"Orphan/vector vượt quota: {report.orphan_vectors}\n"
                    f"Media file: {report.media_files} "
                    f"({format_bytes(report.media_bytes)})\n\n"
                    "Chỉ khi bấm Chạy cleanup thì dữ liệu trên mới bị xóa."
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Chạy cleanup",
                                callback_data="ai:cleanup:run",
                            ),
                            InlineKeyboardButton(
                                text="Hủy",
                                callback_data="ai:operations",
                            ),
                        ]
                    ]
                ),
            )

        @self.router.callback_query(F.data == "ai:cleanup:run")
        async def ai_cleanup_run(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            settings = get_settings()
            async with self.database.session() as session:
                report = await cleanup_storage(
                    session,
                    self.rag.vectors if self.rag else None,
                    media_root=settings.data_dir / "downloads",
                    dry_run=False,
                )
            await callback.answer("Cleanup hoàn tất.")
            await self.bot.send_message(
                callback.from_user.id,
                (
                    f"Đã xóa {report.expired_messages} message, "
                    f"{report.expired_vectors + report.orphan_vectors} vector và "
                    f"{report.media_files} media file."
                ),
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Về vận hành",
                                callback_data="ai:operations",
                            )
                        ]
                    ]
                ),
            )

        @self.router.callback_query(F.data.startswith("ai:toggle:"))
        async def ai_toggle(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.clear()
            enabled = (callback.data or "").endswith(":on")
            async with self.database.session() as session:
                setting = await session.get(AppSetting, "ai_enabled")
                if setting:
                    setting.value = enabled
                else:
                    session.add(
                        AppSetting(
                            key="ai_enabled",
                            value=enabled,
                            description="Bật/tắt lớp AI; chức năng local không bị ảnh hưởng.",
                        )
                    )
            await callback.answer("Đã bật AI." if enabled else "Đã tắt AI.")
            await self._send_ai_menu(
                callback.from_user.id,
                text=(
                    "Đã bật AI. Bạn có thể hỏi hoặc tạo bản tóm tắt."
                    if enabled
                    else "Đã tắt AI. Tìm kiếm local, đồng bộ và chống spam vẫn chạy."
                ),
            )

        @self.router.callback_query(F.data == "ai:help")
        async def ai_help(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.clear()
            await callback.answer()
            enabled = await self._current_ai_enabled()
            await self.bot.send_message(
                callback.from_user.id,
                "CÁCH DÙNG TRỢ LÝ AI\n\n"
                "• Hỏi AI: hỏi tự nhiên; AI chỉ tham khảo chat đã allow, có quyền tìm kiếm "
                "và dữ liệu trong 7×24 giờ gần nhất tính từ lúc bạn hỏi.\n"
                "  Câu trả lời có mã [S1], [S2] và phần dẫn chứng: link bài Telegram nếu có; "
                "nếu không có link thì ghi tên group/channel cùng thời gian đăng.\n"
                "• Tìm trong Telegram: mặc định tìm 7 ngày gần nhất trong SQLite local, "
                "không gọi AI. Có thể dùng after:/before: để chọn khoảng ngày khác.\n"
                "• Tổng hợp toàn bộ ngày: rà mọi tin đã đồng bộ từ group/channel có quyền, "
                "lọc nội dung không phù hợp, loại tin trùng, phân loại, dẫn chứng và liệt kê "
                "nguồn đã tổng hợp. “Hôm qua” là đúng ngày hôm qua theo giờ Việt Nam.\n"
                "• Trạng thái & ngân sách: xem model và chi phí ước tính.\n"
                "• Xuất kiểm kê nguồn học: tải CSV trạng thái nguồn, điền can_hoc/ghi_chu "
                "và gửi lại để tạo hàng đợi sau bước xác nhận.\n"
                "• Tắt AI: không ảnh hưởng đồng bộ, tìm local hay chống spam.\n\n"
                "Bot không tự gửi, sửa hoặc xóa tin chỉ vì câu trả lời của AI.",
                reply_markup=ai_menu_keyboard(enabled=enabled),
            )

        @self.router.callback_query(F.data == "ai:provider")
        async def ai_provider(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.clear()
            await callback.answer()
            provider = self.ai.provider if self.ai else "off"
            await self.bot.send_message(
                callback.from_user.id,
                "NHÀ CUNG CẤP AI\n\n"
                f"Đang dùng: {provider}\n"
                f"Model: {self.ai.model if self.ai else '-'}\n\n"
                "Có thể chọn OpenAI trực tiếp, OpenRouter, Ollama local hoặc tắt AI. "
                "Ollama không cần API key và dữ liệu được xử lý trên máy. "
                "Với provider cloud, bot không nhận API key qua Telegram. "
                "Chọn provider bằng nút bên dưới; thay đổi được áp dụng ngay. "
                "Bạn cũng có thể quản lý, tải và xóa model Ollama trên Telegram. "
                "API key cloud vẫn chỉ được nhập an toàn ngoài Telegram.",
                reply_markup=ai_provider_keyboard(provider=provider),
            )

        @self.router.callback_query(F.data.startswith("ai:provider:set:"))
        async def ai_provider_set(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.clear()
            provider = (callback.data or "").rsplit(":", 1)[-1]
            if provider not in {"openai", "openrouter", "ollama", "off"}:
                await callback.answer("Provider không hợp lệ.", show_alert=True)
                return
            await callback.answer("Đang chuyển nhà cung cấp AI…")
            if not self.ai_provider_switch_handler:
                result = "Bộ chuyển nhà cung cấp AI chưa sẵn sàng."
            else:
                try:
                    result = await self.ai_provider_switch_handler(provider)
                    async with self.database.session() as session:
                        enabled = provider != "off"
                        setting = await session.get(AppSetting, "ai_enabled")
                        if setting:
                            setting.value = enabled
                        else:
                            session.add(
                                AppSetting(
                                    key="ai_enabled",
                                    value=enabled,
                                    description="Bật/tắt toàn bộ lớp AI.",
                                )
                            )
                except (OllamaError, RuntimeError, ValueError) as exc:
                    result = f"Không thể chuyển sang {provider}: {exc}"
            active_provider = self.ai.provider if self.ai else "off"
            await self.bot.send_message(
                callback.from_user.id,
                result,
                reply_markup=ai_provider_keyboard(provider=active_provider),
            )

        @self.router.callback_query(F.data == "ollama:menu")
        async def ollama_menu(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.clear()
            await callback.answer()
            await self._send_ollama_menu(callback.from_user.id)

        @self.router.callback_query(F.data == "ollama:choose:chat")
        async def ollama_choose_chat(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await callback.answer()
            await self._send_ollama_model_choices(
                callback.from_user.id,
                state,
                action="chat",
                title="CHỌN MODEL CHAT OLLAMA",
            )

        @self.router.callback_query(F.data.startswith("ollama:chat:set:"))
        async def ollama_set_chat(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await callback.answer()
            try:
                index = int((callback.data or "").rsplit(":", 1)[-1])
                names = list((await state.get_data()).get("ollama_model_names", []))
                model = validate_model_name(str(names[index]))
            except (IndexError, TypeError, ValueError, OllamaError):
                await self.bot.send_message(callback.from_user.id, "Lựa chọn model đã hết hạn.")
                return
            save_settings_env({"TG_ASSISTANT_OLLAMA_PRIMARY_MODEL": model})
            if self.ai and self.ai.provider == "ollama":
                self.ai.model = model
            await state.clear()
            await self.bot.send_message(
                callback.from_user.id,
                f"Đã chọn model chat Ollama: {model}\n"
                + (
                    "Đã áp dụng ngay cho câu hỏi mới."
                    if self.ai and self.ai.provider == "ollama"
                    else "Bấm “Kích hoạt Ollama” để bắt đầu sử dụng."
                ),
                reply_markup=ollama_menu_keyboard(
                    active=bool(self.ai and self.ai.provider == "ollama")
                ),
            )

        @self.router.callback_query(F.data == "ollama:choose:embedding")
        async def ollama_choose_embedding(
            callback: CallbackQuery,
            state: FSMContext,
        ) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await callback.answer()
            await self._send_ollama_model_choices(
                callback.from_user.id,
                state,
                action="embedding",
                title="CHỌN MODEL EMBEDDING OLLAMA",
            )

        @self.router.callback_query(F.data.startswith("ollama:embedding:set:"))
        async def ollama_set_embedding(
            callback: CallbackQuery,
            state: FSMContext,
        ) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await callback.answer("Đang kiểm tra khả năng embedding…")
            try:
                index = int((callback.data or "").rsplit(":", 1)[-1])
                names = list((await state.get_data()).get("ollama_model_names", []))
                model = validate_model_name(str(names[index]))
            except (IndexError, TypeError, ValueError, OllamaError):
                await self.bot.send_message(callback.from_user.id, "Lựa chọn model đã hết hạn.")
                return
            if not self.ollama:
                await self.bot.send_message(callback.from_user.id, "Ollama chưa được khởi tạo.")
                return
            try:
                dimension = await self.ollama.embedding_dimension(model)
                settings = get_settings()
                if self.ai and self.ai.provider == "ollama":
                    if not self.ollama_activate_handler:
                        raise OllamaError("Không có bộ chuyển provider Ollama.")
                    result = await self.ollama_activate_handler(
                        self.ai.model,
                        model,
                        dimension,
                    )
                else:
                    save_settings_env(
                        {
                            "TG_ASSISTANT_OLLAMA_EMBEDDING_MODEL": model,
                            "TG_ASSISTANT_OLLAMA_VECTOR_SIZE": str(dimension),
                        }
                    )
                    result = (
                        f"Đã chọn embedding Ollama: {model} ({dimension} chiều).\n"
                        f"Model chat dự kiến: {settings.ollama_primary_model}\n"
                        "Bấm “Kích hoạt Ollama” để bắt đầu sử dụng."
                    )
            except (OllamaError, RuntimeError, ValueError) as exc:
                await self.bot.send_message(
                    callback.from_user.id,
                    f"Không thể dùng model này làm embedding: {exc}",
                    reply_markup=ollama_menu_keyboard(
                        active=bool(self.ai and self.ai.provider == "ollama")
                    ),
                )
                return
            await state.clear()
            await self.bot.send_message(
                callback.from_user.id,
                result,
                reply_markup=ollama_menu_keyboard(
                    active=bool(self.ai and self.ai.provider == "ollama")
                ),
            )

        @self.router.callback_query(F.data == "ollama:activate")
        async def ollama_activate(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.clear()
            await callback.answer("Đang kiểm tra model và kích hoạt Ollama…")
            if not self.ollama or not self.ollama_activate_handler:
                await self.bot.send_message(
                    callback.from_user.id,
                    "Ollama chưa sẵn sàng trong tiến trình bot.",
                )
                return
            settings = get_settings()
            try:
                installed = {model.name for model in await self.ollama.list_models()}
                missing = [
                    model
                    for model in (
                        settings.ollama_primary_model,
                        settings.ollama_embedding_model,
                    )
                    if model not in installed
                ]
                if missing:
                    raise OllamaError("Chưa cài model: " + ", ".join(missing))
                dimension = await self.ollama.embedding_dimension(settings.ollama_embedding_model)
                result = await self.ollama_activate_handler(
                    settings.ollama_primary_model,
                    settings.ollama_embedding_model,
                    dimension,
                )
                async with self.database.session() as session:
                    setting = await session.get(AppSetting, "ai_enabled")
                    if setting:
                        setting.value = True
                    else:
                        session.add(
                            AppSetting(
                                key="ai_enabled",
                                value=True,
                                description="Bật/tắt lớp AI.",
                            )
                        )
            except (OllamaError, RuntimeError, ValueError) as exc:
                result = f"Không thể kích hoạt Ollama: {exc}"
            await self.bot.send_message(
                callback.from_user.id,
                result,
                reply_markup=ollama_menu_keyboard(
                    active=bool(self.ai and self.ai.provider == "ollama")
                ),
            )

        @self.router.callback_query(F.data == "ollama:pull")
        async def ollama_pull_prompt(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.set_state(OllamaFlowState.waiting_pull_model)
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                "Gửi tên model muốn tải từ Ollama, ví dụ: gemma3:12b.\n\n"
                "Model có thể chiếm nhiều GB và mất thời gian tải. Bot sẽ yêu cầu "
                "xác nhận trước khi bắt đầu.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Hủy",
                                callback_data="ollama:menu",
                            )
                        ]
                    ]
                ),
            )

        @self.router.message(OllamaFlowState.waiting_pull_model)
        async def ollama_pull_name(message: Message, state: FSMContext) -> None:
            if not self._owner(message):
                return await self._deny(message)
            try:
                model = validate_model_name(message.text or "")
            except OllamaError as exc:
                await message.answer(str(exc))
                return
            await state.update_data(ollama_pull_model=model)
            await message.answer(
                "XÁC NHẬN TẢI MODEL OLLAMA\n\n"
                f"Model: {model}\n\n"
                "Thao tác có thể tải nhiều GB dữ liệu xuống máy.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Xác nhận tải",
                                callback_data="ollama:pull:confirm",
                            ),
                            InlineKeyboardButton(
                                text="Hủy",
                                callback_data="ollama:menu",
                            ),
                        ]
                    ]
                ),
            )

        @self.router.callback_query(F.data == "ollama:pull:confirm")
        async def ollama_pull_confirm(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            data = await state.get_data()
            try:
                model = validate_model_name(str(data.get("ollama_pull_model", "")))
            except OllamaError:
                await callback.answer("Yêu cầu tải đã hết hạn.", show_alert=True)
                return
            await state.clear()
            await callback.answer("Đã bắt đầu tải model.")
            self._run_background_task(self._pull_ollama_model(callback.from_user.id, model))
            await self.bot.send_message(
                callback.from_user.id,
                f"ĐANG TẢI MODEL OLLAMA\n\nModel: {model}\n"
                "Bot sẽ báo lại khi tải xong hoặc gặp lỗi.",
            )

        @self.router.callback_query(F.data == "ollama:choose:delete")
        async def ollama_choose_delete(
            callback: CallbackQuery,
            state: FSMContext,
        ) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await callback.answer()
            await self._send_ollama_model_choices(
                callback.from_user.id,
                state,
                action="delete",
                title="CHỌN MODEL OLLAMA CẦN XÓA",
            )

        @self.router.callback_query(F.data.startswith("ollama:delete:set:"))
        async def ollama_delete_preview(
            callback: CallbackQuery,
            state: FSMContext,
        ) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await callback.answer()
            try:
                index = int((callback.data or "").rsplit(":", 1)[-1])
                names = list((await state.get_data()).get("ollama_model_names", []))
                model = validate_model_name(str(names[index]))
            except (IndexError, TypeError, ValueError, OllamaError):
                await self.bot.send_message(callback.from_user.id, "Lựa chọn model đã hết hạn.")
                return
            settings = get_settings()
            if model in {
                settings.ollama_primary_model,
                settings.ollama_embedding_model,
            }:
                await self.bot.send_message(
                    callback.from_user.id,
                    "Không thể xóa model đang được cấu hình. "
                    "Hãy chọn model chat/embedding khác trước.",
                    reply_markup=ollama_menu_keyboard(
                        active=bool(self.ai and self.ai.provider == "ollama")
                    ),
                )
                return
            await state.update_data(ollama_delete_model=model)
            await self.bot.send_message(
                callback.from_user.id,
                "XÁC NHẬN XÓA MODEL OLLAMA\n\n"
                f"Model: {model}\n\n"
                "File model sẽ bị xóa khỏi máy. Dữ liệu Telegram/SQLite không bị xóa "
                "và model có thể được tải lại sau.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Xác nhận xóa",
                                callback_data="ollama:delete:confirm",
                            ),
                            InlineKeyboardButton(
                                text="Hủy",
                                callback_data="ollama:menu",
                            ),
                        ]
                    ]
                ),
            )

        @self.router.callback_query(F.data == "ollama:delete:confirm")
        async def ollama_delete_confirm(
            callback: CallbackQuery,
            state: FSMContext,
        ) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            data = await state.get_data()
            try:
                model = validate_model_name(str(data.get("ollama_delete_model", "")))
            except OllamaError:
                await callback.answer("Yêu cầu xóa đã hết hạn.", show_alert=True)
                return
            if not self.ollama:
                await callback.answer("Ollama chưa sẵn sàng.", show_alert=True)
                return
            await callback.answer("Đang xóa model…")
            try:
                await self.ollama.delete_model(model)
                result = f"Đã xóa model Ollama: {model}"
            except OllamaError as exc:
                result = f"Không thể xóa model {model}: {exc}"
            await state.clear()
            await self.bot.send_message(
                callback.from_user.id,
                result,
                reply_markup=ollama_menu_keyboard(
                    active=bool(self.ai and self.ai.provider == "ollama")
                ),
            )

        @self.router.message(Command("groups"))
        async def groups(message: Message) -> None:
            if not self._owner(message):
                return await self._deny(message)
            await self._send_groups_hub(message.chat.id)

        @self.router.callback_query(F.data == "groups:show")
        async def show_groups_menu(
            callback: CallbackQuery,
            state: FSMContext,
        ) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.clear()
            await callback.answer()
            await self._send_groups_hub(callback.from_user.id)

        @self.router.callback_query(F.data.startswith("groups:list:"))
        async def show_group_category(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            category = (callback.data or "").rsplit(":", 1)[-1]
            if category not in GROUP_LIST_CATEGORIES:
                await callback.answer("Danh mục không hợp lệ.", show_alert=True)
                return
            await callback.answer()
            await self._send_groups_menu(
                callback.from_user.id,
                category=category,
            )

        @self.router.callback_query(F.data.startswith("groups:page:"))
        async def show_groups_page(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            parts = (callback.data or "").split(":")
            try:
                if len(parts) == 3:
                    category, page = "all", int(parts[2])
                elif len(parts) == 4:
                    category, page = parts[2], int(parts[3])
                else:
                    raise ValueError
            except (ValueError, IndexError):
                await callback.answer("Trang không hợp lệ.", show_alert=True)
                return
            if category not in GROUP_LIST_CATEGORIES:
                await callback.answer("Danh mục không hợp lệ.", show_alert=True)
                return
            await callback.answer()
            await self._send_groups_menu(
                callback.from_user.id,
                page=max(page, 0),
                category=category,
            )

        @self.router.callback_query(F.data == "groups:search")
        async def begin_group_search(
            callback: CallbackQuery,
            state: FSMContext,
        ) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.set_state(GroupSearchState.waiting_query)
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                "Nhập một phần tên group, @username hoặc Chat ID. "
                "Ví dụ: Coin68 hoặc @Group_68Trading.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Hủy tìm kiếm",
                                callback_data="groups:search_cancel",
                            )
                        ]
                    ]
                ),
            )

        @self.router.callback_query(F.data == "groups:search_cancel")
        async def cancel_group_search(
            callback: CallbackQuery,
            state: FSMContext,
        ) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await state.clear()
            await callback.answer("Đã hủy tìm kiếm.")
            await self._send_groups_hub(callback.from_user.id)

        @self.router.message(GroupSearchState.waiting_query)
        async def receive_group_search(
            message: Message,
            state: FSMContext,
        ) -> None:
            if not self._owner(message):
                return await self._deny(message)
            query = (message.text or "").strip()
            if len(query) < 2:
                await message.answer("Hãy nhập ít nhất 2 ký tự.")
                return
            if len(query) > 100 or contains_secret(query):
                await message.answer(
                    "Từ khóa không hợp lệ hoặc giống thông tin bí mật. Hãy nhập tên group ngắn gọn."
                )
                return
            async with self.database.session() as session:
                chats = list((await session.scalars(group_search_query(query, limit=11))).all())
            await state.clear()
            shown, has_more = chats[:10], len(chats) > 10
            if not shown:
                await message.answer(
                    f"Không tìm thấy group khớp với “{query}”.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="Tìm lại",
                                    callback_data="groups:search",
                                ),
                                InlineKeyboardButton(
                                    text="Quản lý nhóm",
                                    callback_data="groups:show",
                                ),
                            ]
                        ]
                    ),
                )
                return
            keyboard = [
                [
                    InlineKeyboardButton(
                        text=(chat.title or f"Group {chat.chat_id}")[:50],
                        callback_data=f"group:open:{chat.chat_id}",
                    )
                ]
                for chat in shown
            ]
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="Tìm lại",
                        callback_data="groups:search",
                    ),
                    InlineKeyboardButton(
                        text="Quản lý nhóm",
                        callback_data="groups:show",
                    ),
                ]
            )
            suffix = "\nCó thêm kết quả; hãy nhập từ khóa cụ thể hơn." if has_more else ""
            await message.answer(
                f"Tìm thấy {len(shown)} group cho “{query}”. "
                f"Chọn group để mở flow quản lý.{suffix}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )

        @self.router.callback_query(F.data == "groups:export_csv")
        async def export_groups_csv(callback: CallbackQuery) -> None:
            if callback.from_user.id != self.owner_id:
                await callback.answer()
                return
            async with self.database.session() as session:
                rows = list((await session.execute(group_rank_query())).all())
                policies = {
                    p.chat_id: p for p in (await session.scalars(select(TelegramChatPolicy))).all()
                }
            content = build_groups_csv(rows, policies)
            filename = f"telegram-groups-{datetime.now().strftime('%Y%m%d-%H%M')}.csv"
            await self.bot.send_document(
                chat_id=callback.from_user.id,
                document=BufferedInputFile(content, filename=filename),
                caption=f"Toàn bộ {len(rows)} group/supergroup, xếp theo mức tương tác.",
            )
            await callback.answer("Đã xuất CSV.")

        @self.router.callback_query(F.data == "groups:dismiss_export")
        async def dismiss_groups_export(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            await callback.answer("Đã bỏ qua.")
            if callback.message:
                await callback.message.edit_reply_markup(reply_markup=None)

        @self.router.callback_query(F.data.startswith("group:open:"))
        async def open_group_flow(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            try:
                chat_id = int((callback.data or "").rsplit(":", 1)[1])
            except (ValueError, IndexError):
                await callback.answer("Chat ID không hợp lệ.", show_alert=True)
                return
            async with self.database.session() as session:
                chat = await session.scalar(
                    select(TelegramChat).where(
                        TelegramChat.chat_id == chat_id,
                        TelegramChat.chat_type.in_(GROUP_CHAT_TYPES),
                    )
                )
                policy = await session.scalar(
                    select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
                )
                source = await session.get(KnowledgeSource, chat_id)
                permissions = (
                    await session.scalars(
                        select(TelegramChatPermission).where(
                            TelegramChatPermission.chat_id == chat_id,
                            TelegramChatPermission.enabled.is_(True),
                        )
                    )
                ).all()
                auto_rule = await session.scalar(
                    select(ModerationRule).where(
                        ModerationRule.chat_id == chat_id,
                        ModerationRule.name == "non_admin_external_link_auto_delete",
                        ModerationRule.enabled.is_(True),
                    )
                )
            if not chat:
                await callback.answer("Không tìm thấy group.", show_alert=True)
                return
            enabled = {row.permission for row in permissions}
            actual_delete = bool((chat.account_rights or {}).get("delete_messages"))
            text = (
                f"QUẢN LÝ GROUP\n\n"
                f"Tên: {chat.title or 'Nhóm không có tiêu đề'}\n"
                f"ID: {chat.chat_id}\n"
                f"Username: {'@' + chat.username if chat.username else '-'}\n"
                f"Allowlist: {'ALLOW' if policy and policy.allowed else 'BLOCK'}\n"
                f"Quyền Telegram xóa tin: {'CÓ' if actual_delete else 'KHÔNG'}\n"
                f"Chống spam: "
                f"{'ĐÃ THIẾT LẬP' if PermissionName.DELETE_ANY_MESSAGES.value in enabled else 'CHƯA THIẾT LẬP'}\n"
                f"AUTO xóa link non-admin: {'ĐANG BẬT' if auto_rule else 'ĐANG TẮT'}\n\n"
                f"Thành viên hỏi AI bằng @your_assistant_username /ask: "
                f"{'ĐANG BẬT' if PermissionName.GROUP_AI_ASK.value in enabled else 'ĐANG TẮT'}\n"
                f"AI mode: {policy.ai_mode if policy else 'inherit'}\n"
                f"Cloud fallback: {'BẬT' if policy and policy.cloud_fallback else 'TẮT'}\n"
                f"Retention: "
                f"{str(policy.retention_days) + ' ngày' if policy and policy.retention_days else 'không giới hạn'}\n"
                f"Quota: "
                f"{policy.max_messages if policy and policy.max_messages else '∞'} message • "
                f"{policy.max_vectors if policy and policy.max_vectors else '∞'} vector • "
                f"{policy.max_storage_mb if policy and policy.max_storage_mb else '∞'} MB\n"
                f"Đang dùng: {source.mysql_message_count if source else 0} message • "
                f"{source.vector_count if source else 0} vector • "
                f"{((source.message_storage_bytes + source.media_storage_bytes) / 1024 / 1024) if source else 0:.1f} MB\n\n"
                "Chọn bước tiếp theo:"
            )
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                text,
                reply_markup=group_actions_keyboard(
                    chat_id,
                    auto_enabled=bool(auto_rule),
                    ai_ask_enabled=PermissionName.GROUP_AI_ASK.value in enabled,
                ),
            )

        @self.router.callback_query(F.data.startswith("group_ai:policy:"))
        async def group_ai_policy(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            try:
                chat_id = int((callback.data or "").rsplit(":", 1)[1])
            except (ValueError, IndexError):
                await callback.answer("Chat ID không hợp lệ.", show_alert=True)
                return
            async with self.database.session() as session:
                chat = await session.scalar(
                    select(TelegramChat).where(TelegramChat.chat_id == chat_id)
                )
                policy = await session.scalar(
                    select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
                )
                source = await session.get(KnowledgeSource, chat_id)
            if not chat or not policy or not policy.allowed:
                await callback.answer("Group chưa được ALLOW.", show_alert=True)
                return
            usage_mb = (
                (source.message_storage_bytes + source.media_storage_bytes) / 1024 / 1024
                if source
                else 0
            )
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                (
                    f"AI POLICY — {chat.title or chat_id}\n\n"
                    f"Mode: {policy.ai_mode}\n"
                    f"Cloud: {policy.preferred_cloud_provider or 'tự chọn'}\n"
                    f"Fallback: {'bật' if policy.cloud_fallback else 'tắt'}\n"
                    f"Retention: "
                    f"{str(policy.retention_days) + ' ngày' if policy.retention_days else 'giữ mãi'}\n"
                    f"Quota: {policy.max_messages or '∞'} message • "
                    f"{policy.max_vectors or '∞'} vector • "
                    f"{policy.max_storage_mb or '∞'} MB\n"
                    f"Đã dùng: {source.mysql_message_count if source else 0} message • "
                    f"{source.vector_count if source else 0} vector • {usage_mb:.1f} MB\n\n"
                    "Local → Cloud và Cloud → Local sẽ tự fallback khi provider đầu lỗi."
                ),
                reply_markup=group_ai_policy_keyboard(chat_id, mode=policy.ai_mode),
            )

        @self.router.callback_query(F.data.startswith("group_ai:mode:"))
        async def group_ai_mode(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            try:
                _prefix, _kind, raw_chat_id, mode = (callback.data or "").split(":")
                chat_id = int(raw_chat_id)
            except (ValueError, IndexError):
                await callback.answer("Dữ liệu mode không hợp lệ.", show_alert=True)
                return
            async with self.database.session() as session:
                current = await session.scalar(
                    select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
                )
                cloud = current.preferred_cloud_provider if current else None
                await self.policy.set_ai_route(
                    session,
                    chat_id,
                    mode=mode,
                    preferred_cloud_provider=cloud,
                    cloud_fallback=mode in {"local_first", "cloud_first"},
                )
            await callback.answer(f"Đã đặt AI mode: {mode}.")
            await self.bot.send_message(
                callback.from_user.id,
                f"AI mode của group {chat_id}: {mode}.",
                reply_markup=group_ai_policy_keyboard(chat_id, mode=mode),
            )

        @self.router.callback_query(F.data.startswith("group_ai:cloud:"))
        async def group_ai_cloud(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            try:
                _prefix, _kind, raw_chat_id, provider = (callback.data or "").split(":")
                chat_id = int(raw_chat_id)
            except (ValueError, IndexError):
                await callback.answer("Dữ liệu provider không hợp lệ.", show_alert=True)
                return
            async with self.database.session() as session:
                current = await session.scalar(
                    select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
                )
                if not current:
                    await callback.answer("Group chưa có policy.", show_alert=True)
                    return
                await self.policy.set_ai_route(
                    session,
                    chat_id,
                    mode=current.ai_mode,
                    preferred_cloud_provider=provider,
                    cloud_fallback=current.cloud_fallback,
                )
            await callback.answer(f"Cloud provider: {provider}.")

        @self.router.callback_query(F.data.startswith("group_ai:quota:"))
        async def group_ai_quota(callback: CallbackQuery, state: FSMContext) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            try:
                _prefix, _kind, raw_chat_id, preset = (callback.data or "").split(":")
                chat_id = int(raw_chat_id)
                if preset == "custom":
                    await state.set_state(AiFlowState.waiting_source_limits)
                    await state.update_data(source_limits_chat_id=chat_id)
                    await callback.answer()
                    await self.bot.send_message(
                        callback.from_user.id,
                        (
                            "Nhập 4 giá trị theo thứ tự:\n"
                            "retention_ngày message MB vector\n\n"
                            "Dùng dấu - cho mục không giới hạn.\n"
                            "Ví dụ: 14 25000 1000 25000"
                        ),
                    )
                    return
                limits = {
                    "light": (10_000, 500, 10_000),
                    "large": (100_000, 5_000, 100_000),
                    "unlimited": (None, None, None),
                }[preset]
            except (ValueError, KeyError):
                await callback.answer("Quota không hợp lệ.", show_alert=True)
                return
            async with self.database.session() as session:
                current = await session.scalar(
                    select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
                )
                await self.policy.set_source_limits(
                    session,
                    chat_id,
                    retention_days=current.retention_days if current else None,
                    max_messages=limits[0],
                    max_storage_mb=limits[1],
                    max_vectors=limits[2],
                )
            await callback.answer(f"Đã áp dụng quota {preset}.")

        @self.router.message(AiFlowState.waiting_source_limits)
        async def group_ai_custom_limits(
            message: Message,
            state: FSMContext,
        ) -> None:
            if not self._owner(message):
                return await self._deny(message)
            values = (message.text or "").strip().split()
            if len(values) != 4:
                await message.answer("Cần đúng 4 giá trị: retention_ngày message MB vector.")
                return

            def parse_limit(raw: str) -> int | None:
                if raw.casefold() in {"-", "none", "null", "khong"}:
                    return None
                value = int(raw)
                if value <= 0:
                    raise ValueError
                return value

            try:
                retention, messages, storage_mb, vectors = [parse_limit(raw) for raw in values]
            except ValueError:
                await message.answer("Mỗi giá trị phải là số nguyên dương hoặc dấu -.")
                return
            data = await state.get_data()
            chat_id = int(data["source_limits_chat_id"])
            async with self.database.session() as session:
                updated = await self.policy.set_source_limits(
                    session,
                    chat_id,
                    retention_days=retention,
                    max_messages=messages,
                    max_storage_mb=storage_mb,
                    max_vectors=vectors,
                )
            await state.clear()
            await message.answer(
                (
                    f"Đã lưu giới hạn cho group {chat_id}:\n"
                    f"Retention: {retention or '∞'} ngày\n"
                    f"Message: {messages or '∞'} • MB: {storage_mb or '∞'} • "
                    f"Vector: {vectors or '∞'}"
                ),
                reply_markup=group_ai_policy_keyboard(chat_id, mode=updated.ai_mode),
            )

        @self.router.callback_query(F.data.startswith("group_ai:retention:"))
        async def group_ai_retention(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            try:
                _prefix, _kind, raw_chat_id, raw_days = (callback.data or "").split(":")
                chat_id = int(raw_chat_id)
                retention_days = None if raw_days == "forever" else int(raw_days)
            except ValueError:
                await callback.answer("Retention không hợp lệ.", show_alert=True)
                return
            async with self.database.session() as session:
                current = await session.scalar(
                    select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
                )
                if not current:
                    await callback.answer("Group chưa có policy.", show_alert=True)
                    return
                await self.policy.set_source_limits(
                    session,
                    chat_id,
                    retention_days=retention_days,
                    max_messages=current.max_messages,
                    max_storage_mb=current.max_storage_mb,
                    max_vectors=current.max_vectors,
                )
            await callback.answer("Đã đặt retention. Cleanup sẽ áp dụng ở chu kỳ kế tiếp.")

        @self.router.callback_query(
            F.data.startswith("group_ai:ask_on:") | F.data.startswith("group_ai:ask_off:")
        )
        async def set_group_ai_ask_flow(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            data = callback.data or ""
            enabled = data.startswith("group_ai:ask_on:")
            try:
                chat_id = int(data.rsplit(":", 1)[1])
            except (ValueError, IndexError):
                await callback.answer("Chat ID không hợp lệ.", show_alert=True)
                return
            async with self.database.session() as session:
                chat = await session.scalar(
                    select(TelegramChat).where(
                        TelegramChat.chat_id == chat_id,
                        TelegramChat.chat_type.in_(GROUP_CHAT_TYPES),
                    )
                )
                policy = await session.scalar(
                    select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
                )
                if not chat:
                    await callback.answer("Không tìm thấy group.", show_alert=True)
                    return
                if enabled and (not policy or not policy.allowed):
                    await callback.answer(
                        "Hãy ALLOW group trước khi bật thành viên hỏi AI.",
                        show_alert=True,
                    )
                    return
                if enabled and not bool((chat.account_rights or {}).get("send_messages")):
                    await callback.answer(
                        "Tài khoản @your_assistant_username hiện không có quyền gửi tin trong group.",
                        show_alert=True,
                    )
                    return
                action = await self.actions.create(
                    session,
                    action_type="set_group_ai_ask",
                    requested_by=self.owner_id,
                    chat_id=chat_id,
                    payload={"enabled": enabled},
                    preview=(
                        (
                            f"BẬT THÀNH VIÊN HỎI AI\n"
                            f"Group: {chat.title or chat_id} ({chat_id})\n"
                            "Cú pháp: @your_assistant_username /ask <câu hỏi>\n"
                            "AI sẽ trả lời ngay trong group từ toàn bộ kho group/channel "
                            "đã học và được cấp quyền tìm kiếm, mặc định trong 7 ngày gần nhất.\n"
                            "Nếu hỏi “nhóm này đang thảo luận gì?”, AI chỉ đọc tối đa "
                            "100 tin gần nhất trong 7 ngày của chính group này.\n"
                            "Nếu hỏi “@username đã nói về chủ đề gì?”, Telegram Client "
                            "truy cứu và đồng bộ SQLite tối đa 100 tin gần nhất của riêng "
                            "tài khoản đó trong group.\n"
                            "Lưu ý: nội dung tổng hợp từ các nguồn đã học có thể xuất hiện "
                            "trong group này. Câu hỏi tin tức/cập nhật sẽ kèm nguồn; "
                            "câu hỏi kiến thức thông thường sẽ ẩn phần DẪN CHỨNG. "
                            "Mỗi thành viên được giới hạn 3 câu/phút."
                        )
                        if enabled
                        else (
                            f"TẮT THÀNH VIÊN HỎI AI\n"
                            f"Group: {chat.title or chat_id} ({chat_id})\n"
                            "Ngừng nhận cú pháp @your_assistant_username /ask trong group này."
                        )
                    ),
                    reason=(
                        "Owner cấp standing authorization cho thành viên group hỏi AI"
                        if enabled
                        else "Owner thu hồi quyền thành viên group hỏi AI"
                    ),
                )
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                action.preview or "Xác nhận thay đổi quyền hỏi AI trong group.",
                reply_markup=action_confirmation_keyboard(action.action_id),
            )

        @self.router.callback_query(
            F.data.startswith("moderation:link_auto_on:")
            | F.data.startswith("moderation:link_auto_off:")
            | F.data.startswith("moderation:auto_on:")
            | F.data.startswith("moderation:auto_off:")
        )
        async def set_auto_moderation_flow(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            data = callback.data or ""
            enabled = data.startswith(("moderation:link_auto_on:", "moderation:auto_on:"))
            try:
                chat_id = int(data.rsplit(":", 1)[1])
            except (ValueError, IndexError):
                await callback.answer("Chat ID không hợp lệ.", show_alert=True)
                return
            async with self.database.session() as session:
                chat = await session.scalar(
                    select(TelegramChat).where(
                        TelegramChat.chat_id == chat_id,
                        TelegramChat.chat_type.in_(GROUP_CHAT_TYPES),
                    )
                )
                if not chat:
                    await callback.answer("Không tìm thấy group.", show_alert=True)
                    return
                if enabled and not bool((chat.account_rights or {}).get("delete_messages")):
                    await callback.answer(
                        "Tài khoản hiện không có quyền Telegram để xóa tin.",
                        show_alert=True,
                    )
                    return
                action = await self.actions.create(
                    session,
                    action_type="set_link_spam_auto_moderation",
                    requested_by=self.owner_id,
                    chat_id=chat_id,
                    payload={
                        "enabled": enabled,
                        "scope": "new_messages_only",
                    },
                    preview=(
                        (
                            f"BẬT AUTO XÓA LINK NON-ADMIN\n"
                            f"Group: {chat.title} ({chat_id})\n"
                            "Chỉ xóa tin MỚI của thành viên thường khi có URL, "
                            "link ẩn, www, t.me hoặc tên miền.\n"
                            "Tin không có link được giữ. Link do bạn, creator/admin "
                            "hoặc admin ẩn danh đăng được giữ.\n"
                            "Không xóa ngược tin cũ. Lỗi xác minh → giữ tin."
                        )
                        if enabled
                        else (
                            f"TẮT AUTO XÓA LINK NON-ADMIN\n"
                            f"Group: {chat.title} ({chat_id})\n"
                            "Ngừng tự động xóa tin mới có link của thành viên thường. "
                            "Quyền moderation thủ công vẫn được giữ."
                        )
                    ),
                    reason=(
                        "Owner bật standing authorization xóa external link của non-admin"
                        if enabled
                        else "Owner tắt auto link moderation"
                    ),
                )
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                action.preview or "Xác nhận thay đổi AUTO moderation.",
                reply_markup=action_confirmation_keyboard(action.action_id),
            )

        @self.router.callback_query(F.data.startswith("moderation:setup:"))
        async def setup_moderation_flow(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            try:
                chat_id = int((callback.data or "").rsplit(":", 1)[1])
            except (ValueError, IndexError):
                await callback.answer("Chat ID không hợp lệ.", show_alert=True)
                return
            async with self.database.session() as session:
                chat = await session.scalar(
                    select(TelegramChat).where(TelegramChat.chat_id == chat_id)
                )
                if not chat:
                    await callback.answer("Không tìm thấy group.", show_alert=True)
                    return
                action = await self.actions.create(
                    session,
                    action_type="setup_moderation",
                    requested_by=self.owner_id,
                    chat_id=chat_id,
                    payload={"mode": "review_first"},
                    preview=(
                        f"Group: {chat.title} ({chat_id})\n"
                        "Bật allowlist và quyền đọc/giám sát\n"
                        "Bật đồng bộ, tìm kiếm, moderation và xóa mọi tin\n"
                        "Chế độ: phát hiện và xem trước; KHÔNG tự xóa"
                    ),
                    reason="Thiết lập flow chống spam có xác nhận",
                )
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                f"XEM TRƯỚC THIẾT LẬP\n\n{action.preview}",
                reply_markup=action_confirmation_keyboard(action.action_id),
            )

        @self.router.callback_query(F.data.startswith("moderation:sync:"))
        async def sync_group_flow(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            try:
                chat_id = int((callback.data or "").rsplit(":", 1)[1])
            except (ValueError, IndexError):
                await callback.answer("Chat ID không hợp lệ.", show_alert=True)
                return
            async with self.database.session() as session:
                decision = await self.policy.evaluate(
                    session,
                    PolicyContext(
                        self.owner_id,
                        self.owner_id,
                        chat_id,
                        PermissionName.SYNC_HISTORY,
                    ),
                )
                if not decision.allowed:
                    await callback.answer(
                        "Hãy hoàn tất Thiết lập chống spam trước.",
                        show_alert=True,
                    )
                    return
                chat = await session.scalar(
                    select(TelegramChat).where(TelegramChat.chat_id == chat_id)
                )
                action = await self.actions.create(
                    session,
                    action_type="sync_chat_history",
                    requested_by=self.owner_id,
                    chat_id=chat_id,
                    payload={"limit": 1000},
                    preview=(
                        f"Đồng bộ tối đa 1.000 tin mới nhất từ hiện tại trở về trước trong "
                        f"{chat.title if chat else chat_id}.\n"
                        "Chỉ lưu dữ liệu local theo quyền đã cấp."
                    ),
                    reason="Owner yêu cầu đồng bộ từ flow moderation",
                )
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                f"XEM TRƯỚC ĐỒNG BỘ\n\n{action.preview}",
                reply_markup=action_confirmation_keyboard(action.action_id),
            )

        @self.router.callback_query(F.data.startswith("moderation:scan:"))
        async def scan_group_links(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            try:
                chat_id = int((callback.data or "").rsplit(":", 1)[1])
            except (ValueError, IndexError):
                await callback.answer("Chat ID không hợp lệ.", show_alert=True)
                return
            async with self.database.session() as session:
                decision = await self.policy.evaluate(
                    session,
                    PolicyContext(
                        self.owner_id,
                        self.owner_id,
                        chat_id,
                        PermissionName.SEARCH_MESSAGES,
                    ),
                )
                if not decision.allowed:
                    await callback.answer(
                        "Hãy hoàn tất Thiết lập chống spam trước.",
                        show_alert=True,
                    )
                    return
                results = await self.search.keyword(
                    session,
                    "has:link",
                    owner_id=self.owner_id,
                    actor_id=self.owner_id,
                    chat_ids=[chat_id],
                    limit=20,
                )
            if not results:
                await callback.answer()
                await self.bot.send_message(
                    callback.from_user.id,
                    "Chưa tìm thấy link trong dữ liệu local. Nếu vừa xác nhận đồng bộ, "
                    "hãy đợi vài giây rồi bấm Quét lại.",
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="Quét lại",
                                    callback_data=f"moderation:scan:{chat_id}",
                                )
                            ],
                            [
                                InlineKeyboardButton(
                                    text="Quay lại group",
                                    callback_data=f"group:open:{chat_id}",
                                )
                            ],
                        ]
                    ),
                )
                return
            shown = results[:10]
            lines = [
                f"{index}. Message #{result.message_id}\n{result.text[:250]}"
                for index, result in enumerate(shown, start=1)
            ]
            keyboard = [
                [
                    InlineKeyboardButton(
                        text=f"Xem để xóa #{result.message_id}",
                        callback_data=(f"moderation:delete:{chat_id}:{result.message_id}"),
                    )
                ]
                for result in shown
            ]
            keyboard.append(
                [
                    InlineKeyboardButton(
                        text="Quét lại",
                        callback_data=f"moderation:scan:{chat_id}",
                    ),
                    InlineKeyboardButton(
                        text="Quay lại group",
                        callback_data=f"group:open:{chat_id}",
                    ),
                ]
            )
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                "CÁC TIN CHỨA LINK — MỚI NHẤT TRƯỚC\n\n" + "\n\n".join(lines),
                reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
            )

        @self.router.callback_query(F.data.startswith("moderation:delete:"))
        async def review_delete_from_flow(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            try:
                _prefix, _action, raw_chat, raw_message = (callback.data or "").split(":")
                chat_id, message_id = int(raw_chat), int(raw_message)
            except (ValueError, IndexError):
                await callback.answer("Dữ liệu tin nhắn không hợp lệ.", show_alert=True)
                return
            async with self.database.session() as session:
                stored = await session.scalar(
                    select(TelegramMessage).where(
                        TelegramMessage.chat_id == chat_id,
                        TelegramMessage.message_id == message_id,
                    )
                )
                chat = await session.scalar(
                    select(TelegramChat).where(TelegramChat.chat_id == chat_id)
                )
                if not stored:
                    await callback.answer(
                        "Không còn bản local của tin nhắn.",
                        show_alert=True,
                    )
                    return
                preview = (
                    f"Chat: {chat.title if chat else '-'}\n"
                    f"Chat ID: {chat_id}\nMessage ID: {message_id}\n"
                    f"Sender ID: {stored.sender_id}\nThời gian: {stored.sent_at}\n"
                    f"Nội dung: {str(redact(stored.text or '[media]'))[:700]}"
                )
                action = await self.actions.create(
                    session,
                    action_type="delete_message",
                    requested_by=self.owner_id,
                    chat_id=chat_id,
                    message_id=message_id,
                    payload={"delete_any": True},
                    preview=preview,
                    reason="Spam hoặc liên kết không phù hợp; owner chọn từ flow",
                )
            await callback.answer()
            await self.bot.send_message(
                callback.from_user.id,
                f"XEM TRƯỚC XÓA TIN\n\n{action.preview}\n\n"
                "Tin chỉ bị xóa sau khi bạn bấm Xác nhận.",
                reply_markup=action_confirmation_keyboard(action.action_id),
            )

        @self.router.callback_query(F.data.startswith("action:confirm:"))
        async def confirm_action_button(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            action_id = (callback.data or "").removeprefix("action:confirm:")
            try:
                async with self.database.session() as session:
                    action = await self.actions.confirm(
                        session,
                        action_id,
                        self.owner_id,
                    )
                    action_type, chat_id, action_payload = (
                        action.action_type,
                        action.chat_id,
                        dict(action.payload),
                    )
            except (PermissionError, TimeoutError, ValueError) as exc:
                await callback.answer(str(exc), show_alert=True)
                return
            await callback.answer("Đã xác nhận.")
            if callback.message:
                await callback.message.edit_reply_markup(reply_markup=None)
            followup = None
            text = "Đã xác nhận; worker đang thực thi sau khi kiểm tra quyền lần cuối."
            if chat_id is not None and action_type == "setup_moderation":
                followup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Đồng bộ 1.000 tin mới nhất",
                                callback_data=f"moderation:sync:{chat_id}",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="Quay lại group",
                                callback_data=f"group:open:{chat_id}",
                            )
                        ],
                    ]
                )
                text += "\nĐợi vài giây, sau đó bấm Đồng bộ."
            elif chat_id is not None and action_type in {
                "set_admin_only_auto_moderation",
                "set_link_spam_auto_moderation",
            }:
                enabled = bool(action_payload.get("enabled"))
                followup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Quay lại group",
                                callback_data=f"group:open:{chat_id}",
                            )
                        ]
                    ]
                )
                text += (
                    "\nĐợi vài giây. AUTO sẽ chỉ xóa tin mới có link của non-admin."
                    if enabled
                    else "\nĐợi vài giây. AUTO xóa link sẽ được tắt."
                )
            elif chat_id is not None and action_type == "sync_chat_history":
                followup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Quét các tin chứa link",
                                callback_data=f"moderation:scan:{chat_id}",
                            )
                        ]
                    ]
                )
                text += "\nĐợi quá trình đồng bộ hoàn tất, sau đó bấm Quét link."
            elif (
                action_type == "enable_group_learning_bulk"
                or chat_id is not None
                and action_type == "enable_group_learning"
            ):
                followup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Xem tiến độ học",
                                callback_data="ai:learn:status",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="Về menu AI",
                                callback_data="ai:menu",
                            )
                        ],
                    ]
                )
                text += (
                    "\nCác group đã được đưa vào hàng đợi nền. Bấm Xem tiến độ học "
                    "để theo dõi số group hoàn tất, đang chạy hoặc bị lỗi."
                )
            elif chat_id is not None and action_type == "delete_message":
                followup = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Quét lại",
                                callback_data=f"moderation:scan:{chat_id}",
                            )
                        ]
                    ]
                )
            await self.bot.send_message(
                callback.from_user.id,
                text,
                reply_markup=followup,
            )

        @self.router.callback_query(F.data.startswith("action:cancel:"))
        async def cancel_action_button(callback: CallbackQuery) -> None:
            if not self._owner_callback(callback):
                await callback.answer()
                return
            action_id = (callback.data or "").removeprefix("action:cancel:")
            try:
                async with self.database.session() as session:
                    await self.actions.cancel(
                        session,
                        action_id,
                        self.owner_id,
                    )
            except ValueError as exc:
                await callback.answer(str(exc), show_alert=True)
                return
            await callback.answer("Đã hủy.")
            if callback.message:
                await callback.message.edit_reply_markup(reply_markup=None)
            await self.bot.send_message(callback.from_user.id, "Hành động đã được hủy.")

        @self.router.message(Command("group_info"))
        async def group_info(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            try:
                chat_id = int(command.args or "")
            except ValueError:
                return await message.answer("Dùng: /group_info <chat_id>")
            async with self.database.session() as session:
                chat = await session.scalar(
                    select(TelegramChat).where(TelegramChat.chat_id == chat_id)
                )
                policy = await session.scalar(
                    select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
                )
            await message.answer(
                f"{chat.title if chat else 'Không rõ'}\nID: {chat_id}\nLoại: {chat.chat_type if chat else '-'}\nTrạng thái: {'ALLOW' if policy and policy.allowed else 'BLOCK'}"
            )

        @self.router.message(Command("group_allow", "group_block"))
        async def group_toggle(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            try:
                parts = (command.args or "").split()
                chat_id = int(parts[0])
            except (ValueError, IndexError):
                return await message.answer(
                    "Dùng: /group_allow <chat_id> hoặc /group_block <chat_id> <keep|archive|delete>"
                )
            allowed = message.text.startswith("/group_allow")
            async with self.database.session() as session:
                chat = await session.scalar(
                    select(TelegramChat).where(TelegramChat.chat_id == chat_id)
                )
                current = await session.scalar(
                    select(TelegramChatPolicy).where(TelegramChatPolicy.chat_id == chat_id)
                )
                if not chat:
                    return await message.answer("Không tìm thấy metadata của Chat ID này.")
                memory_action = "keep"
                if not allowed:
                    if len(parts) < 2 or parts[1] not in {"keep", "archive", "delete"}:
                        return await message.answer(
                            "Khi block, chọn cách xử lý memory: "
                            "/group_block <chat_id> <keep|archive|delete>"
                        )
                    memory_action = parts[1]
                before = "ALLOW" if current and current.allowed else "BLOCK"
                after = "ALLOW" if allowed else "BLOCK"
                action = await self.actions.create(
                    session,
                    action_type="set_chat_allowed",
                    requested_by=self.owner_id,
                    chat_id=chat_id,
                    payload={"allowed": allowed, "memory_action": memory_action},
                    preview=(
                        f"{chat.title} ({chat_id})\nTrước: {before}\nSau: {after}\n"
                        f"Memory khi block: {memory_action}"
                    ),
                    reason="Thay đổi allowlist từ control bot",
                )
            await message.answer(f"Chờ xác nhận:\n{action.preview}\n/confirm {action.action_id}")

        @self.router.message(Command("permissions"))
        async def permissions(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            try:
                chat_id = int(command.args or "")
            except ValueError:
                return await message.answer("Dùng: /permissions <chat_id>")
            async with self.database.session() as session:
                rows = (
                    await session.scalars(
                        select(TelegramChatPermission).where(
                            TelegramChatPermission.chat_id == chat_id
                        )
                    )
                ).all()
            state = {r.permission: r.enabled for r in rows}
            await message.answer(
                "\n".join(
                    f"{p.value}: {'on' if state.get(p.value, False) else 'off'}"
                    for p in PermissionName
                )
            )

        @self.router.message(Command("permission_set"))
        async def permission_set(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            try:
                raw_id, raw_permission, raw_state = (command.args or "").split()
                chat_id, permission = int(raw_id), PermissionName(raw_permission)
                enabled = {"on": True, "off": False}[raw_state.lower()]
            except (ValueError, KeyError):
                return await message.answer("Dùng: /permission_set <chat_id> <permission> <on|off>")
            async with self.database.session() as session:
                current = await session.scalar(
                    select(TelegramChatPermission).where(
                        TelegramChatPermission.chat_id == chat_id,
                        TelegramChatPermission.permission == permission.value,
                    )
                )
                action_type = (
                    "set_group_ai_ask"
                    if permission is PermissionName.GROUP_AI_ASK
                    else "set_chat_permission"
                )
                action = await self.actions.create(
                    session,
                    action_type=action_type,
                    requested_by=self.owner_id,
                    chat_id=chat_id,
                    payload=(
                        {"enabled": enabled}
                        if permission is PermissionName.GROUP_AI_ASK
                        else {"permission": permission.value, "enabled": enabled}
                    ),
                    preview=(
                        f"Chat {chat_id}\n{permission.value}: "
                        f"{'on' if current and current.enabled else 'off'} → "
                        f"{'on' if enabled else 'off'}"
                        + (
                            "\nKhi bật, thành viên có thể hỏi @your_assistant_username và "
                            "nhận nội dung từ kho dự án đã học ngay trong group."
                            if permission is PermissionName.GROUP_AI_ASK and enabled
                            else ""
                        )
                    ),
                    reason="Thay đổi quyền từ control bot",
                )
            await message.answer(f"Chờ xác nhận:\n{action.preview}\n/confirm {action.action_id}")

        @self.router.message(Command("permission_template"))
        async def template(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            try:
                raw_id, name = (command.args or "").split()
                chat_id = int(raw_id)
            except ValueError:
                return await message.answer(
                    "Dùng: /permission_template <chat_id> <read_only|knowledge|task_management|moderation>"
                )
            if name == "moderation":
                await message.answer(
                    "Cảnh báo: mẫu moderation có quyền phá hủy. delete_any vẫn tắt và mỗi lần xóa vẫn cần xác nhận."
                )
            async with self.database.session() as session:
                action = await self.actions.create(
                    session,
                    action_type="apply_permission_template",
                    requested_by=self.owner_id,
                    chat_id=chat_id,
                    payload={"template": name},
                    preview=f"Chat {chat_id}\nÁp dụng mẫu: {name}",
                    reason="Áp dụng mẫu quyền từ control bot",
                )
            await message.answer(f"Chờ xác nhận:\n{action.preview}\n/confirm {action.action_id}")

        @self.router.message(Command("search"))
        async def search(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            query = (command.args or "").strip()
            async with self.database.session() as session:
                ids = list(
                    (
                        await session.scalars(
                            select(TelegramChatPolicy.chat_id).where(
                                TelegramChatPolicy.allowed.is_(True)
                            )
                        )
                    ).all()
                )
                results = await self.search.keyword(
                    session,
                    query,
                    owner_id=self.owner_id,
                    actor_id=self.owner_id,
                    chat_ids=ids,
                    default_after=rag_time_window()[0],
                )
            await message.answer(
                "\n\n".join(
                    f"[{r.chat_id}/{r.message_id}] sender={r.sender_id} "
                    f"time={r.sent_at} source={r.source}\n{r.text[:500]}"
                    for r in results
                )
                or (
                    "Không tìm thấy đủ thông tin trong khoảng thời gian đã chọn "
                    "(mặc định là 7 ngày gần nhất) từ dữ liệu Telegram đã được cấp quyền."
                )
            )

        @self.router.message(Command("send"))
        async def send(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            try:
                raw_id, text = (command.args or "").split(maxsplit=1)
                chat_id = int(raw_id)
            except ValueError:
                return await message.answer("Dùng: /send <chat_id> <nội dung>")
            async with self.database.session() as session:
                action = await self.actions.create(
                    session,
                    action_type="send_message",
                    requested_by=self.owner_id,
                    chat_id=chat_id,
                    payload={"text": text},
                    preview=text[:500],
                    reason="Yêu cầu trực tiếp từ owner",
                )
            await message.answer(
                f"Chờ xác nhận gửi tới {chat_id}:\n{text[:500]}\n/confirm {action.action_id}"
            )

        @self.router.message(Command("ask"))
        async def ask(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            question = (command.args or "").strip()
            if not question:
                await self._send_ai_menu(
                    message.chat.id,
                    text="Chọn “Hỏi AI”, rồi gửi câu hỏi tự nhiên của bạn.",
                )
                return
            if contains_secret(question):
                await message.answer("Nội dung có vẻ chứa secret nên bot không gửi nó tới AI.")
                return
            await message.answer("Đã nhận câu hỏi; đang rà dữ liệu Telegram trong 7 ngày gần nhất…")
            answer = await self._ask_ai(question)
            chunks = telegram_html_chunks(answer)
            for index, chunk in enumerate(chunks):
                if not await self._answer_still_authorized(answer):
                    return
                await message.answer(
                    chunk,
                    parse_mode="HTML",
                    reply_markup=(
                        ai_followup_keyboard(mode="ask") if index == len(chunks) - 1 else None
                    ),
                )

        @self.router.message(Command("price"))
        async def price(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            query = (command.args or "").strip()
            if not query:
                return await message.answer("Dùng: /price <mã hoặc tên coin>\nVí dụ: /price BTC")
            answer = await self._coin_price_answer(query)
            chunks = telegram_html_chunks(answer)
            for index, chunk in enumerate(chunks):
                await message.answer(
                    chunk,
                    parse_mode="HTML",
                    reply_markup=(
                        ai_menu_keyboard(enabled=await self._current_ai_enabled())
                        if index == len(chunks) - 1
                        else None
                    ),
                )

        @self.router.message(Command("digest"))
        async def digest(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            period = (command.args or "today").strip().lower()
            period = period if period in {"today", "yesterday"} else "today"
            output = await self._digest(period)
            if output is None:
                return
            label = "HÔM NAY" if period == "today" else "HÔM QUA"
            chunks = telegram_html_chunks(f"## TỔNG HỢP {label}\n\n{output.text}")
            for index, chunk in enumerate(chunks):
                markup = (
                    ai_menu_keyboard(enabled=await self._current_ai_enabled())
                    if index == len(chunks) - 1 else None
                )
                if not await self._digest_still_authorized(output):
                    return
                await message.answer(
                    chunk,
                    parse_mode="HTML",
                    reply_markup=markup,
                )

        @self.router.message(
            Command("ai_status", "ai_usage", "ai_budget", "ai_model", "ai_on", "ai_off")
        )
        async def ai_status(message: Message) -> None:
            if not self._owner(message):
                return await self._deny(message)
            async with self.database.session() as session:
                command_name = (message.text or "").split()[0].split("@")[0]
                if command_name in {"/ai_on", "/ai_off"}:
                    enabled = command_name == "/ai_on"
                    setting = await session.get(AppSetting, "ai_enabled")
                    if setting:
                        setting.value = enabled
                    else:
                        session.add(
                            AppSetting(
                                key="ai_enabled",
                                value=enabled,
                                description="Bật/tắt lớp AI; chức năng local không bị ảnh hưởng.",
                            )
                        )
            enabled = await self._current_ai_enabled()
            await message.answer(
                await self._ai_status_text(),
                reply_markup=ai_menu_keyboard(enabled=enabled),
            )

        @self.router.message(Command("remember"))
        async def remember(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            raw = (command.args or "").strip()
            if not raw:
                return await message.answer("Dùng: /remember [private|global|chat:<id>] <nội dung>")
            first, _, remainder = raw.partition(" ")
            if first in {"private", "global"} or first.startswith("chat:"):
                scope, content = first, remainder.strip()
            else:
                scope, content = "private", raw
            if scope.startswith("chat:"):
                try:
                    scope_type, scope_id = "chat", str(int(scope.split(":", 1)[1]))
                except ValueError:
                    return await message.answer("Scope chat cần numeric Chat ID.")
            else:
                scope_type, scope_id = scope, None
            async with self.database.session() as session:
                if scope_type == "chat":
                    decision = await self.policy.evaluate(
                        session,
                        PolicyContext(
                            self.owner_id,
                            self.owner_id,
                            int(scope_id),
                            PermissionName.CREATE_MEMORIES,
                        ),
                    )
                    if not decision.allowed:
                        return await message.answer(
                            f"Không thể tạo memory cho chat: {decision.reason.value}"
                        )
                action = await self.actions.create(
                    session,
                    action_type="create_memory",
                    requested_by=self.owner_id,
                    payload={
                        "content": content,
                        "memory_type": "semantic",
                        "scope_type": scope_type,
                        "scope_id": scope_id,
                    },
                    preview=(
                        f"Nội dung: {content[:1000]}\nPhạm vi: "
                        f"{scope_type}{':' + scope_id if scope_id else ''}\nNguồn: owner"
                    ),
                    reason="Owner yêu cầu ghi nhớ",
                )
            await message.answer(
                f"Chờ xác nhận memory:\n{action.preview}\n/confirm {action.action_id}"
            )

        @self.router.message(Command("memory", "memory_search"))
        async def memory(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            query = (command.args or "").strip()
            chat_id = None
            if query.startswith("chat:"):
                scope, _, query = query.partition(" ")
                try:
                    chat_id = int(scope.split(":", 1)[1])
                except ValueError:
                    return await message.answer("Dùng: /memory_search chat:<id> <query>")
            async with self.database.session() as session:
                if query:
                    rows = await self.memories.search(session, query, chat_id=chat_id)
                else:
                    rows = list(
                        (
                            await session.scalars(
                                select(AiMemory)
                                .where(AiMemory.status == "active")
                                .order_by(AiMemory.updated_at.desc())
                                .limit(30)
                            )
                        ).all()
                    )
            await message.answer(
                "\n\n".join(
                    f"{row.id} | {row.scope_type}:{row.scope_id or '-'}\n{row.content[:500]}"
                    for row in rows
                )
                or "Không có memory phù hợp."
            )

        @self.router.message(Command("memory_forget"))
        async def memory_forget(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            memory_id = (command.args or "").strip()
            if not memory_id:
                return await message.answer("Dùng: /memory_forget <id>")
            async with self.database.session() as session:
                row = await session.get(AiMemory, memory_id)
                if not row or row.status != "active":
                    return await message.answer("Không tìm thấy memory đang hoạt động.")
                action = await self.actions.create(
                    session,
                    action_type="forget_memory",
                    requested_by=self.owner_id,
                    payload={"memory_id": memory_id},
                    preview=f"Ẩn/forget memory {memory_id}: {row.content[:500]}",
                    reason="Owner yêu cầu quên memory",
                )
            await message.answer(f"Chờ xác nhận:\n{action.preview}\n/confirm {action.action_id}")

        @self.router.message(Command("delete"))
        async def delete(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            try:
                parts = (command.args or "").split(maxsplit=3)
                chat_id, message_id = int(parts[0]), int(parts[1])
                delete_any = len(parts) > 2 and parts[2].lower() == "any"
                reason = parts[3] if len(parts) > 3 else "Yêu cầu trực tiếp từ owner"
            except (ValueError, IndexError):
                return await message.answer(
                    "Dùng: /delete <chat_id> <message_id> <own|any> [lý do]"
                )
            async with self.database.session() as session:
                stored = await session.scalar(
                    select(TelegramMessage).where(
                        TelegramMessage.chat_id == chat_id,
                        TelegramMessage.message_id == message_id,
                    )
                )
                chat = await session.scalar(
                    select(TelegramChat).where(TelegramChat.chat_id == chat_id)
                )
                if not stored:
                    return await message.answer(
                        "Không có bản local của tin nhắn; chạy sync trước để có preview an toàn."
                    )
                preview = (
                    f"Chat: {chat.title if chat else '-'}\nChat ID: {chat_id}\n"
                    f"Message ID: {message_id}\nSender ID: {stored.sender_id}\n"
                    f"Thời gian: {stored.sent_at}\n"
                    f"Nội dung: {str(redact(stored.text or '[media]'))[:500]}"
                )
                action = await self.actions.create(
                    session,
                    action_type="delete_message",
                    requested_by=self.owner_id,
                    chat_id=chat_id,
                    message_id=message_id,
                    payload={"delete_any": delete_any},
                    preview=preview,
                    reason=reason,
                )
            await message.answer(
                f"HÀNH ĐỘNG PHÁ HỦY\n{action.preview}\n"
                f"Phạm vi: {'tin bất kỳ' if delete_any else 'tin của mình'}\n"
                f"Lý do: {reason}\n/confirm {action.action_id}"
            )

        @self.router.message(Command("edit"))
        async def edit(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            try:
                raw_chat, raw_message, text = (command.args or "").split(maxsplit=2)
                chat_id, message_id = int(raw_chat), int(raw_message)
            except ValueError:
                return await message.answer("Dùng: /edit <chat_id> <message_id> <nội dung>")
            async with self.database.session() as session:
                stored = await session.scalar(
                    select(TelegramMessage).where(
                        TelegramMessage.chat_id == chat_id,
                        TelegramMessage.message_id == message_id,
                        TelegramMessage.is_outgoing.is_(True),
                    )
                )
                if not stored:
                    return await message.answer("Chỉ sửa tin do chính tài khoản này gửi.")
                action = await self.actions.create(
                    session,
                    action_type="edit_message",
                    requested_by=self.owner_id,
                    chat_id=chat_id,
                    message_id=message_id,
                    payload={"text": text},
                    preview=f"Trước: {(stored.text or '')[:400]}\nSau: {text[:400]}",
                    reason="Yêu cầu sửa tin từ owner",
                )
            await message.answer(f"Chờ xác nhận:\n{action.preview}\n/confirm {action.action_id}")

        @self.router.message(Command("pin"))
        async def pin(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            try:
                raw_chat, raw_message = (command.args or "").split()
                chat_id, message_id = int(raw_chat), int(raw_message)
            except ValueError:
                return await message.answer("Dùng: /pin <chat_id> <message_id>")
            async with self.database.session() as session:
                action = await self.actions.create(
                    session,
                    action_type="pin_message",
                    requested_by=self.owner_id,
                    chat_id=chat_id,
                    message_id=message_id,
                    payload={},
                    preview=f"Ghim tin {chat_id}/{message_id}",
                    reason="Yêu cầu ghim từ owner",
                )
            await message.answer(f"Chờ xác nhận:\n{action.preview}\n/confirm {action.action_id}")

        @self.router.message(Command("pending_actions"))
        async def pending_actions(message: Message) -> None:
            if not self._owner(message):
                return await self._deny(message)
            async with self.database.session() as session:
                rows = (
                    await session.scalars(
                        select(PendingAction)
                        .where(PendingAction.status == "pending")
                        .order_by(PendingAction.created_at.desc())
                        .limit(30)
                    )
                ).all()
            await message.answer(
                "\n".join(
                    f"{a.action_id} | {a.action_type} | {a.chat_id}/{a.message_id} | hết hạn {a.expires_at}"
                    for a in rows
                )
                or "Không có action chờ."
            )

        @self.router.message(Command("task_add"))
        async def task_add(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            raw = (command.args or "").strip()
            title, separator, due_phrase = raw.partition("|")
            title = title.strip()
            if not title:
                return await message.answer("Dùng: /task_add <nội dung> [| thời gian tiếng Việt]")
            due_at = None
            if separator:
                try:
                    due_at = parse_vietnamese_datetime(due_phrase.strip()).astimezone(UTC)
                except ValueError as exc:
                    return await message.answer(str(exc))
            async with self.database.session() as session:
                task = await self.tasks.create(session, title, due_at=due_at)
            await message.answer(f"Đã tạo task {task.id}")

        @self.router.message(Command("task_list"))
        async def task_list(message: Message) -> None:
            if not self._owner(message):
                return await self._deny(message)
            async with self.database.session() as session:
                tasks = await self.tasks.list(session)
            await message.answer(
                "\n".join(f"{t.id} | {t.status} | {t.title}" for t in tasks[:30])
                or "Không có task."
            )

        @self.router.message(Command("task_done"))
        async def task_done(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            async with self.database.session() as session:
                await self.tasks.update_status(
                    session, (command.args or "").strip(), TaskStatus.DONE
                )
            await message.answer("Đã hoàn tất.")

        @self.router.message(Command("task_info"))
        async def task_info(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            task_id = (command.args or "").strip()
            async with self.database.session() as session:
                task = await session.get(Task, task_id)
            if not task:
                return await message.answer("Không tìm thấy task.")
            await message.answer(
                f"{task.id}\n{task.title}\nTrạng thái: {task.status}\n"
                f"Ưu tiên: {task.priority}\nDeadline: {task.due_at or '-'}\n"
                f"Mô tả: {task.description or '-'}"
            )

        @self.router.message(Command("task_edit"))
        async def task_edit(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            task_id, separator, remainder = (command.args or "").partition(" ")
            title, due_separator, due_phrase = remainder.partition("|")
            if not separator or not title.strip():
                return await message.answer("Dùng: /task_edit <id> <tiêu đề mới> [| thời gian]")
            due_at = None
            if due_separator:
                try:
                    due_at = parse_vietnamese_datetime(due_phrase.strip()).astimezone(UTC)
                except ValueError as exc:
                    return await message.answer(str(exc))
            async with self.database.session() as session:
                task = await self.tasks.update(session, task_id, title=title.strip(), due_at=due_at)
            await message.answer(f"Đã cập nhật task {task.id}.")

        @self.router.message(Command("task_delete"))
        async def task_delete(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            task_id = (command.args or "").strip()
            async with self.database.session() as session:
                task = await session.get(Task, task_id)
                if not task:
                    return await message.answer("Không tìm thấy task.")
                action = await self.actions.create(
                    session,
                    action_type="delete_task",
                    requested_by=self.owner_id,
                    payload={"task_id": task_id},
                    preview=f"Đánh dấu cancelled task {task_id}: {task.title}",
                    reason="Owner yêu cầu xóa task",
                )
            await message.answer(f"Chờ xác nhận:\n{action.preview}\n/confirm {action.action_id}")

        @self.router.message(Command("today", "overdue", "inbox"))
        async def task_views(message: Message) -> None:
            if not self._owner(message):
                return await self._deny(message)
            now = datetime.now(UTC)
            command_name = (message.text or "").split()[0].split("@")[0]
            query = select(Task).where(
                Task.status.not_in([TaskStatus.DONE.value, TaskStatus.CANCELLED.value])
            )
            if command_name == "/today":
                query = query.where(
                    Task.due_at >= now.replace(hour=0, minute=0, second=0, microsecond=0),
                    Task.due_at
                    < now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1),
                )
            elif command_name == "/overdue":
                query = query.where(Task.due_at < now)
            else:
                query = query.where(Task.status == TaskStatus.INBOX.value)
            async with self.database.session() as session:
                rows = (
                    await session.scalars(
                        query.order_by(Task.due_at.is_(None), Task.due_at).limit(30)
                    )
                ).all()
            await message.answer(
                "\n".join(
                    f"{row.id} | {row.status} | {row.due_at or '-'} | {row.title}" for row in rows
                )
                or "Không có task."
            )

        @self.router.message(Command("remind"))
        async def remind(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            time_phrase, separator, text = (command.args or "").partition("|")
            if not separator or not text.strip():
                return await message.answer("Dùng: /remind <thời gian tiếng Việt> | <nội dung>")
            try:
                remind_at = parse_vietnamese_datetime(time_phrase.strip()).astimezone(UTC)
            except ValueError as exc:
                return await message.answer(str(exc))
            if remind_at <= datetime.now(UTC):
                return await message.answer("Thời gian nhắc phải ở tương lai.")
            async with self.database.session() as session:
                reminder = Reminder(remind_at=remind_at, message=text.strip())
                session.add(reminder)
                await session.flush()
            await message.answer(f"Đã lên lịch {reminder.id} lúc {remind_at}.")

        @self.router.message(Command("projects"))
        async def projects(message: Message) -> None:
            if not self._owner(message):
                return await self._deny(message)
            async with self.database.session() as session:
                rows = (
                    await session.scalars(select(Project).order_by(Project.name).limit(50))
                ).all()
            await message.answer(
                "\n".join(f"{row.id} | {row.status} | {row.name}" for row in rows)
                or "Chưa có project."
            )

        @self.router.message(Command("confirm"))
        async def confirm(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            async with self.database.session() as session:
                action = await self.actions.confirm(
                    session, (command.args or "").strip(), self.owner_id
                )
            await message.answer(
                f"Đã xác nhận {action.action_id}; worker sẽ thực thi sau khi kiểm tra quyền lần cuối."
            )

        @self.router.message(Command("cancel"))
        async def cancel(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            async with self.database.session() as session:
                await self.actions.cancel(session, (command.args or "").strip(), self.owner_id)
            await message.answer("Đã hủy.")

        @self.router.message(Command("audit"))
        async def audit(message: Message, command: CommandObject) -> None:
            if not self._owner(message):
                return await self._deny(message)
            chat_id = None
            if command.args:
                try:
                    chat_id = int(command.args.strip())
                except ValueError:
                    return await message.answer("Dùng: /audit [chat_id]")
            query = select(AuditLog).order_by(AuditLog.occurred_at.desc()).limit(30)
            if chat_id is not None:
                query = query.where(AuditLog.target_id.like(f"{chat_id}/%"))
            async with self.database.session() as session:
                rows = (await session.scalars(query)).all()
            await message.answer(
                "\n".join(
                    f"{row.occurred_at} | {row.action} | {row.outcome} | {row.target_id}"
                    for row in rows
                )
                or "Chưa có audit log."
            )

        @self.router.message()
        async def natural_language(message: Message) -> None:
            if not self._owner(message):
                return await self._deny(message)
            question = (message.text or "").strip()
            if not question:
                return
            if contains_secret(question):
                await message.answer(
                    "Phát hiện nội dung giống secret; mình không lặp lại, không lưu memory "
                    "và không gửi tới AI. Hãy thu hồi secret nếu cần và dùng "
                    "`tg-assistant reconfigure` trong terminal."
                )
                return
            if price_asset := extract_price_asset(question):
                answer = await self._coin_price_answer(price_asset)
                chunks = telegram_html_chunks(answer)
                for index, chunk in enumerate(chunks):
                    await message.answer(
                        chunk,
                        parse_mode="HTML",
                        reply_markup=(
                            ai_followup_keyboard(mode="ask") if index == len(chunks) - 1 else None
                        ),
                    )
                return
            async with self.database.session() as session:
                chat_ids = list(
                    (
                        await session.scalars(
                            select(TelegramChatPolicy.chat_id).where(
                                TelegramChatPolicy.allowed.is_(True)
                            )
                        )
                    ).all()
                )
                if await self._ai_enabled(session) and self.rag and self.ai and self.ai.available:
                    try:
                        answer = await self.rag.answer(
                            session,
                            question,
                            actor_id=self.owner_id,
                            owner_id=self.owner_id,
                            chat_ids=chat_ids,
                        )
                    except AuthorizationRevoked:
                        return
                    except RuntimeError as exc:
                        answer = str(exc)
                    except Exception:
                        answer = "AI tạm lỗi; dùng /search để tìm local."
                else:
                    results = await self.search.keyword(
                        session,
                        question,
                        owner_id=self.owner_id,
                        actor_id=self.owner_id,
                        chat_ids=chat_ids,
                        limit=10,
                        default_after=rag_time_window()[0],
                    )
                    answer = (
                        "\n\n".join(
                            f"[{row.chat_id}/{row.message_id}] {row.text[:500]}" for row in results
                        )
                        or "AI đang tắt và không có kết quả keyword phù hợp trong 7 ngày gần nhất."
                    )
            chunks = telegram_html_chunks(answer)
            for index, chunk in enumerate(chunks):
                if not await self._answer_still_authorized(answer):
                    return
                await message.answer(
                    chunk,
                    parse_mode="HTML",
                    reply_markup=(
                        ai_followup_keyboard(mode="ask") if index == len(chunks) - 1 else None
                    ),
                )

    async def run(self) -> None:
        if not self._admitted():
            raise _ManagementUnavailable()
        if self._polling_runner is not None:
            await self._polling_runner(self.dp, self.bot)
        elif self._owns_bot:
            await self.dp.start_polling(
                self.bot, allowed_updates=self.dp.resolve_used_update_types()
            )
        else:
            # A borrowed SDK belongs to one controlled poller. Aiogram's default
            # polling shutdown would also close that owner's HTTP session.
            raise _ManagementUnavailable()

    async def close(self) -> None:
        self._closing = True
        tasks = tuple(self._background_tasks | self._handler_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._request_gate is not None:
            self.bot.session.middleware.unregister(self._request_gate)
            self._request_gate = None
        if self._owns_bot:
            await self.bot.session.close()
