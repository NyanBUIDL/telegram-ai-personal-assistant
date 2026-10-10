import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowsClockwise,
  Brain,
  CaretLeft,
  CaretRight,
  ClockCounterClockwise,
  Database,
  DownloadSimple,
  FloppyDisk,
  LockKey,
  MagnifyingGlass,
  ShieldCheck,
  SignOut,
  Trash,
  Warning,
} from "@phosphor-icons/react";

import { api } from "../api.js";
import {
  formatBytes,
  formatNumber,
  formatRelative,
  humanize,
  statusTone,
} from "../format.js";
import { useDebouncedValue, useResource } from "../hooks.js";
import {
  Badge,
  EmptyState,
  ErrorState,
  LoadingState,
  PanelHeader,
  Toggle,
} from "../ui.jsx";
import { useDialogA11y } from "../useDialogA11y.js";

const PERMISSION_GROUPS = [
  ["Đọc và đồng bộ", ["read_messages", "sync_history", "monitor_new_messages", "search_messages"]],
  ["AI và tri thức", ["summarize", "analyze_files", "auto_knowledge", "group_ai_ask"]],
  ["Công việc và memory", ["extract_tasks", "create_memories", "auto_task_suggestion"]],
  ["Gửi và chỉnh sửa", ["send_messages", "edit_own_messages"]],
  [
    "Moderation",
    [
      "delete_own_messages",
      "delete_any_messages",
      "pin_messages",
      "moderate_messages",
      "auto_moderation",
    ],
  ],
  ["Tải dữ liệu", ["download_media"]],
];

const PERMISSION_HELP = {
  read_messages: "Đọc nội dung tin nhắn đã được cấp quyền.",
  sync_history: "Đồng bộ lịch sử gần nhất từ Telegram vào SQLite.",
  monitor_new_messages: "Theo dõi và lưu tin mới sau khi ứng dụng chạy.",
  search_messages: "Cho phép tìm kiếm nội dung trong nguồn này.",
  summarize: "Cho phép AI tóm tắt dữ liệu của nguồn.",
  analyze_files: "Cho phép phân tích tệp đã tải từ nguồn.",
  auto_knowledge: "Tự đồng bộ và tạo embedding để nguồn tham gia bộ não chung.",
  group_ai_ask: "Cho phép thành viên gọi @your_assistant_username /ask trong group.",
  extract_tasks: "Nhận diện công việc, hạn chót và quyết định.",
  create_memories: "Cho phép tạo ghi nhớ từ nội dung phù hợp.",
  auto_task_suggestion: "Gợi ý task nhưng không tự tạo khi chưa xác nhận.",
  send_messages: "Cho phép gửi tin sau bước preview và xác nhận.",
  edit_own_messages: "Chỉnh sửa tin do tài khoản của bạn đã gửi.",
  delete_own_messages: "Xóa tin do tài khoản của bạn đã gửi.",
  delete_any_messages: "Xóa tin của người khác khi tài khoản có quyền admin.",
  pin_messages: "Ghim tin khi tài khoản có quyền Telegram phù hợp.",
  moderate_messages: "Dùng công cụ kiểm duyệt thủ công.",
  auto_moderation: "Áp dụng rule kiểm duyệt tự động đang bật.",
  download_media: "Tải media về máy theo retention và quota.",
};

const AI_MODES = [
  ["inherit", "Theo cấu hình toàn cục"],
  ["local_only", "LOCAL ONLY"],
  ["local_first", "Local trước, cloud fallback"],
  ["cloud_only", "Cloud only"],
  ["cloud_first", "Cloud trước, local fallback"],
  ["off", "Tắt AI riêng nguồn"],
];
const GROUP_COLUMNS = [
  ["policy", "Policy"],
  ["ai", "AI mode"],
  ["ask", "/ask"],
  ["auto", "AUTO link"],
  ["learning", "Học dữ liệu"],
  ["metrics", "Tin / vector"],
];

function learningLabel(value) {
  const labels = {
    not_learned: "CHƯA HỌC",
    queued: "ĐANG CHỜ",
    running: "ĐANG HỌC",
    learned: "ĐÃ HỌC",
    learned_warning: "ĐÃ HỌC · CẬP NHẬT LỖI",
    reconciliation_required: "CẦN ĐỐI CHIẾU INDEX",
    no_content: "KHÔNG CÓ NỘI DUNG",
    failed: "LỖI",
    paused: "TẠM DỪNG",
    pause_requested: "ĐANG TẠM DỪNG",
  };
  return labels[value] || humanize(value);
}

export function GroupsView({ refreshKey, onOpenGroup }) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("all");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [chatType, setChatType] = useState("");
  const [policyStatus, setPolicyStatus] = useState("");
  const [learningStatus, setLearningStatus] = useState("");
  const [sortBy, setSortBy] = useState("last_seen");
  const [sortDir, setSortDir] = useState("desc");
  const [inactiveDays, setInactiveDays] = useState(60);
  const [viewName, setViewName] = useState("");
  const [savingView, setSavingView] = useState(false);
  const [density, setDensity] = useState("comfortable");
  const [visibleColumns, setVisibleColumns] = useState(
    () => GROUP_COLUMNS.map(([value]) => value),
  );
  const debouncedQuery = useDebouncedValue(query);
  const preferences = useResource(api.preferences, [], refreshKey);
  const resource = useResource(
    () =>
      category === "inactive"
        ? api.groupRecommendations(inactiveDays).then((result) => ({
            ...result,
            page: 1,
            pages: 1,
            page_size: result.total,
          }))
        : api.groups({
        category: category === "inactive" ? "all" : category,
        query: debouncedQuery,
        chatType,
        policyStatus,
        learningStatus,
        activityState: category === "inactive" ? "inactive" : "",
        inactiveDays,
        sortBy,
        sortDir,
        page,
        pageSize,
          }),
    [
      category,
      debouncedQuery,
      chatType,
      policyStatus,
      learningStatus,
      inactiveDays,
      sortBy,
      sortDir,
      page,
      pageSize,
    ],
    refreshKey,
  );

  const categories = [
    ["ai", "Nhóm đã bật AI"],
    ["permissions", "Có quyền · chưa bật AI"],
    ["inactive", "Đề xuất ngưng hoạt động"],
    ["all", "Tất cả nhóm"],
  ];

  useEffect(() => {
    if (!preferences.data) return;
    setDensity(preferences.data.density || "comfortable");
    if (preferences.data.visible_group_columns?.length) {
      setVisibleColumns(preferences.data.visible_group_columns);
    }
  }, [preferences.data]);

  const selectCategory = (value) => {
    setCategory(value);
    setPage(1);
  };

  const currentView = {
    category,
    chatType,
    policyStatus,
    learningStatus,
    sortBy,
    sortDir,
    inactiveDays,
    pageSize,
    density,
    visibleColumns,
  };

  const saveView = async () => {
    if (!viewName.trim()) return;
    setSavingView(true);
    try {
      const current = preferences.data || {};
      const saved = (current.saved_views || []).filter(
        (item) => item.name !== viewName.trim(),
      );
      await api.updatePreferences({
        ...current,
        saved_views: [...saved, { name: viewName.trim(), page: "groups", state: currentView }],
        group_page_size: pageSize,
        density,
        visible_group_columns: visibleColumns,
      });
      setViewName("");
      await preferences.reload();
    } finally {
      setSavingView(false);
    }
  };

  const applyView = (name) => {
    const selected = preferences.data?.saved_views?.find((item) => item.name === name);
    if (!selected?.state) return;
    const state = selected.state;
    setCategory(state.category || "all");
    setChatType(state.chatType || "");
    setPolicyStatus(state.policyStatus || "");
    setLearningStatus(state.learningStatus || "");
    setSortBy(state.sortBy || "last_seen");
    setSortDir(state.sortDir || "desc");
    setInactiveDays(Number(state.inactiveDays) || 60);
    setPageSize(Number(state.pageSize) || 10);
    setDensity(state.density || "comfortable");
    if (state.visibleColumns?.length) setVisibleColumns(state.visibleColumns);
    setPage(1);
  };

  const saveDisplayPreference = async (nextDensity, nextColumns) => {
    setDensity(nextDensity);
    setVisibleColumns(nextColumns);
    const current = preferences.data || {};
    await api.updatePreferences({
      ...current,
      density: nextDensity,
      visible_group_columns: nextColumns,
      group_page_size: pageSize,
    });
    await preferences.reload();
  };

  const columnVisible = (name) => visibleColumns.includes(name);

  const keepRecommendedGroup = async (chatId) => {
    setSavingView(true);
    try {
      const current = preferences.data || {};
      await api.keepRecommendedGroup(current, chatId);
      await Promise.all([preferences.reload(), resource.reload()]);
    } finally {
      setSavingView(false);
    }
  };

  return (
    <section className="panel page-panel">
      <PanelHeader
        eyebrow="TELEGRAM DIRECTORY · LIVE"
        title="Nhóm và channel"
        action={<Badge tone="teal">{formatNumber(resource.data?.total)} NGUỒN</Badge>}
      />
      <div className="filter-bar">
        <label className="search-field">
          <MagnifyingGlass size={20} />
          <input
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setPage(1);
            }}
            placeholder="Tìm tên, @username hoặc Chat ID…"
          />
        </label>
        <div className="category-tabs" role="tablist" aria-label="Phân loại nhóm">
          {categories.map(([value, label]) => (
            <button
              key={value}
              role="tab"
              aria-selected={category === value}
              className={category === value ? "is-active" : ""}
              onClick={() => selectCategory(value)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="advanced-filter-bar" aria-label="Lọc và sắp xếp phía server">
        <label>
          <span>Loại</span>
          <select value={chatType} onChange={(event) => { setChatType(event.target.value); setPage(1); }}>
            <option value="">Tất cả</option>
            <option value="group">Group</option>
            <option value="supergroup">Supergroup</option>
            <option value="channel">Channel</option>
          </select>
        </label>
        <label>
          <span>Policy</span>
          <select value={policyStatus} onChange={(event) => { setPolicyStatus(event.target.value); setPage(1); }}>
            <option value="">Tất cả</option>
            <option value="allow">ALLOW</option>
            <option value="block">BLOCK</option>
          </select>
        </label>
        <label>
          <span>Trạng thái học</span>
          <select value={learningStatus} onChange={(event) => { setLearningStatus(event.target.value); setPage(1); }}>
            <option value="">Tất cả</option>
            <option value="learned">Đã học</option>
            <option value="not_learned">Chưa học</option>
            <option value="queued">Đang chờ</option>
            <option value="running">Đang học</option>
            <option value="failed">Lỗi</option>
            <option value="no_content">Không có nội dung</option>
          </select>
        </label>
        <label>
          <span>Sắp xếp</span>
          <select value={sortBy} onChange={(event) => { setSortBy(event.target.value); setPage(1); }}>
            <option value="last_seen">Quan sát gần nhất</option>
            <option value="activity">Tin gần nhất</option>
            <option value="title">Tên</option>
            <option value="type">Loại</option>
            <option value="policy">Policy</option>
            <option value="learning">Trạng thái học</option>
            <option value="messages">Số tin</option>
            <option value="vectors">Số vector</option>
          </select>
        </label>
        <label>
          <span>Chiều</span>
          <select value={sortDir} onChange={(event) => { setSortDir(event.target.value); setPage(1); }}>
            <option value="desc">Giảm dần</option>
            <option value="asc">Tăng dần</option>
          </select>
        </label>
        <label>
          <span>Số dòng</span>
          <select value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1); }}>
            {[10, 25, 50, 100].map((value) => <option key={value}>{value}</option>)}
          </select>
        </label>
        {category === "inactive" ? (
          <label>
            <span>Không hoạt động</span>
            <select value={inactiveDays} onChange={(event) => { setInactiveDays(Number(event.target.value)); setPage(1); }}>
              {[7, 30, 60, 90, 180].map((value) => <option value={value} key={value}>{value} ngày</option>)}
            </select>
          </label>
        ) : null}
      </div>
      <div className="saved-view-bar">
        <label>
          <span>Chế độ xem đã lưu</span>
          <select defaultValue="" onChange={(event) => applyView(event.target.value)}>
            <option value="">Chọn chế độ xem…</option>
            {(preferences.data?.saved_views || [])
              .filter((item) => item.page === "groups")
              .map((item) => <option key={item.name}>{item.name}</option>)}
          </select>
        </label>
        <label className="saved-view-name">
          <span>Tên chế độ xem mới</span>
          <input value={viewName} onChange={(event) => setViewName(event.target.value)} placeholder="Ví dụ: Channel chưa học" maxLength={60} />
        </label>
        <button className="button button--outline" disabled={!viewName.trim() || savingView} onClick={saveView}>
          <FloppyDisk size={18} />
          Lưu chế độ xem
        </button>
        <button
          className="button button--outline"
          disabled={savingView}
          onClick={() =>
            saveDisplayPreference(
              density === "compact" ? "comfortable" : "compact",
              visibleColumns,
            )
          }
        >
          Mật độ: {density === "compact" ? "Gọn" : "Thoáng"}
        </button>
        <details className="column-chooser">
          <summary>Chọn cột</summary>
          <div>
            {GROUP_COLUMNS.map(([value, label]) => (
              <label key={value}>
                <input
                  type="checkbox"
                  checked={columnVisible(value)}
                  onChange={(event) => {
                    const next = event.target.checked
                      ? [...visibleColumns, value]
                      : visibleColumns.filter((item) => item !== value);
                    saveDisplayPreference(density, next);
                  }}
                />
                {label}
              </label>
            ))}
          </div>
        </details>
      </div>

      {resource.loading && !resource.data ? <LoadingState label="Đang tải danh sách nhóm…" /> : null}
      {resource.error && !resource.data ? (
        <ErrorState error={resource.error} onRetry={resource.reload} />
      ) : null}
      {resource.data?.items?.length ? (
        <div className="table-scroll">
          <table className={`group-table group-directory-table ${density === "compact" ? "is-compact" : ""}`}>
            <thead>
              <tr>
                <th>Nhóm / channel</th>
                {columnVisible("policy") ? <th>Policy</th> : null}
                {columnVisible("ai") ? <th>AI mode</th> : null}
                {columnVisible("ask") ? <th>/ask</th> : null}
                {columnVisible("auto") ? <th>AUTO link</th> : null}
                {columnVisible("learning") ? <th>Học dữ liệu</th> : null}
                {columnVisible("metrics") ? <th>Tin / vector</th> : null}
                <th><span className="sr-only">Thao tác</span></th>
              </tr>
            </thead>
            <tbody>
              {resource.data.items.map((group) => (
                <tr key={group.chat_id}>
                  <td>
                    <div className="group-name">
                      <span className={`group-avatar group-avatar--${group.chat_type}`}>
                        {group.chat_type === "channel" ? "CH" : "GR"}
                      </span>
                      <div>
                        <b>{group.title || "Chưa có tên"}</b>
                      <small>
                          {group.chat_id} · {group.chat_type} ·{" "}
                          {group.username ? `@${group.username.replace(/^@/, "")}` : "không username"}
                        </small>
                        {category === "inactive" ? (
                          <small className="table-sub">
                            {group.recommendation?.reason ||
                              `Tin gần nhất: ${formatRelative(group.activity?.last_message_at)}`}
                          </small>
                        ) : null}
                      </div>
                    </div>
                  </td>
                  {columnVisible("policy") ? <td>
                    <Badge tone={group.policy.allowed ? "success" : "magenta"}>
                      {group.policy.allowed ? "ALLOW" : "BLOCK"}
                    </Badge>
                  </td> : null}
                  {columnVisible("ai") ? <td><Badge tone="paper">{String(group.policy.ai_mode).toUpperCase()}</Badge></td> : null}
                  {columnVisible("ask") ? <td>
                    <Badge tone={group.group_ai_ask ? "teal" : "paper"}>
                      {group.group_ai_ask ? "ON" : "OFF"}
                    </Badge>
                  </td> : null}
                  {columnVisible("auto") ? <td>
                    <Badge tone={group.auto_link_moderation ? "yellow" : "paper"}>
                      {group.auto_link_moderation ? "AUTO" : "OFF"}
                    </Badge>
                  </td> : null}
                  {columnVisible("learning") ? <td>
                    <Badge tone={statusTone(group.knowledge.status)}>
                      {learningLabel(group.knowledge.status)}
                    </Badge>
                    <small className="table-sub">
                      {formatRelative(group.knowledge.last_learned_at)}
                    </small>
                  </td> : null}
                  {columnVisible("metrics") ? <td>
                    <b>{formatNumber(group.knowledge.mysql_message_count)}</b>
                    <small className="table-sub">
                      {formatNumber(group.knowledge.vector_count)} vector
                    </small>
                  </td> : null}
                  <td>
                    <div className="row-actions">
                      {category === "inactive" ? (
                        <button
                          className="button button--small button--outline"
                          disabled={savingView}
                          onClick={() => keepRecommendedGroup(group.chat_id)}
                        >
                          Luôn giữ
                        </button>
                      ) : null}
                      <button
                        className="button button--small button--outline"
                        onClick={() => onOpenGroup(group.chat_id)}
                      >
                        Quản lý
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : resource.data ? (
        <EmptyState
          title="Không tìm thấy nhóm"
          description="Thử đổi từ khóa hoặc chọn danh mục khác."
        />
      ) : null}

      {resource.data ? (
        <div className="table-footer">
          <span>
            Trang {resource.data.page}/{Math.max(1, resource.data.pages)} ·{" "}
            {formatNumber(resource.data.total)} nguồn
          </span>
          <div className="pagination">
            <button
              disabled={page <= 1}
              onClick={() => setPage((value) => Math.max(1, value - 1))}
            >
              <CaretLeft size={18} />
              Trước
            </button>
            <b>{page}</b>
            <button
              disabled={page >= Math.max(1, resource.data.pages)}
              onClick={() => setPage((value) => value + 1)}
            >
              Sau
              <CaretRight size={18} />
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function PolicyAction({
  title,
  description,
  active,
  label,
  disabled,
  onChange,
}) {
  return (
    <div className="ops-setting-row">
      <div>
        <b>{title}</b>
        <span>{description}</span>
      </div>
      <Toggle
        active={active}
        label={label}
        disabled={disabled}
        onChange={onChange}
      />
    </div>
  );
}

function HistoryLinkDeletePreviewModal({
  preview,
  candidates,
  loading,
  error,
  saving,
  triggerRef,
  onClose,
  onRetry,
  onChange,
  onDelete,
  onAnalyze,
}) {
  const dialogRef = useDialogA11y(Boolean(preview), onClose, triggerRef);
  if (!preview) return null;

  const items = candidates?.items || [];
  const total = candidates?.total || 0;
  const pageSize = candidates?.page_size || 50;
  const page = candidates?.page || preview.page;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const selectedCount = preview.selectAll
    ? Math.max(0, total - preview.excluded.length)
    : preview.selected.length;
  const isSelected = (messageId) =>
    preview.selectAll
      ? !preview.excluded.includes(messageId)
      : preview.selected.includes(messageId);
  const setMessageSelected = (messageId, nextChecked) => {
    onChange((current) => {
      if (!current) return current;
      if (current.selectAll) {
        const excluded = nextChecked
          ? current.excluded.filter((id) => id !== messageId)
          : [...current.excluded, messageId];
        return { ...current, excluded };
      }
      const selected = nextChecked
        ? [...current.selected, messageId]
        : current.selected.filter((id) => id !== messageId);
      return { ...current, selected };
    });
  };
  const pageFullySelected = items.length > 0 && items.every((item) => isSelected(item.message_id));
  const togglePage = (checked) => {
    onChange((current) => {
      if (!current) return current;
      const ids = items.map((item) => item.message_id);
      if (current.selectAll) {
        const excluded = checked
          ? current.excluded.filter((id) => !ids.includes(id))
          : [...new Set([...current.excluded, ...ids])];
        return { ...current, excluded };
      }
      const selected = checked
        ? [...new Set([...current.selected, ...ids])]
        : current.selected.filter((id) => !ids.includes(id));
      return { ...current, selected };
    });
  };
  const selectAll = () =>
    onChange((current) =>
      current ? { ...current, selectAll: true, selected: [], excluded: [] } : current,
    );
  const clearSelection = () =>
    onChange((current) =>
      current ? { ...current, selectAll: false, selected: [], excluded: [] } : current,
    );

  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <section
        ref={dialogRef}
        className="modal history-delete-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="history-delete-title"
        tabIndex={-1}
      >
        <header className="modal-header">
          <div>
            <p className="eyebrow">TELEGRAM · DELETE PREVIEW</p>
            <h2 id="history-delete-title">
              {preview.mode === "all_links"
                ? "Post có link"
                : preview.mode === "promotion_links"
                  ? "Link + promotion"
                : preview.mode === "sender"
                    ? "Người đăng / channel"
                    : preview.mode === "images"
                      ? "Post có hình ảnh"
                      : "Tên / từ khóa trong post"}
            </h2>
            <span className="history-delete-modal-note">
              Chọn những post cần xóa. Chưa có hành động nào được gửi lên Telegram ở bước này.
            </span>
          </div>
          <button className="icon-button modal-close" aria-label="Đóng" onClick={onClose}>×</button>
        </header>

        {preview.mode === "sender" ? (
          <div className="history-delete-keyword-filter">
            <div>
              <span className="eyebrow">TELEGRAM · SENDER LOOKUP</span>
              <b>Lọc theo người đăng hoặc channel liên kết</b>
              <small>
                Nhập <code>@handle</code> để tra trực tiếp Telegram, hoặc tên channel đã có
                trong danh sách nguồn. Chỉ post của đúng người đăng mới hiện ra.
              </small>
            </div>
            <input
              value={preview.senderQuery}
              maxLength={255}
              onChange={(event) =>
                onChange((current) => ({ ...current, senderQuery: event.target.value }))
              }
              placeholder="Ví dụ: @Coin68News hoặc Coin68 News"
              aria-label="Tên hoặc handle người đăng"
            />
            <button
              className="button button--primary"
              disabled={loading || preview.senderQuery.trim().length < 1}
              onClick={() =>
                onChange((current) => ({
                  ...current,
                  appliedSenderQuery: current.senderQuery,
                  page: 1,
                  selectAll: false,
                  selected: [],
                  excluded: [],
                  aiResults: {},
                }))
              }
            >
              <MagnifyingGlass size={18} weight="bold" /> Tìm người đăng
            </button>
          </div>
        ) : null}

        {preview.mode === "keywords" ? (
          <div className="history-delete-keyword-filter">
            <div>
              <span className="eyebrow">MYSQL · KHÔNG DÙNG AI</span>
              <b>Lọc theo tên hoặc từ khóa trong nội dung</b>
              <small>Nhập nhiều từ khóa bằng dấu phẩy. Post khớp ít nhất một từ sẽ hiện ra.</small>
            </div>
            <input
              value={preview.keywordTerms}
              maxLength={1_000}
              onChange={(event) =>
                onChange((current) => ({ ...current, keywordTerms: event.target.value }))
              }
              placeholder="Ví dụ: Binance, OKX, airdrop"
              aria-label="Tên hoặc từ khóa cần xóa"
            />
            <button
              className="button button--primary"
              disabled={loading || preview.keywordTerms.trim().length < 1}
              onClick={() =>
                onChange((current) => ({
                  ...current,
                  appliedKeywordTerms: current.keywordTerms,
                  page: 1,
                  selectAll: false,
                  selected: [],
                  excluded: [],
                  aiResults: {},
                }))
              }
            >
              <MagnifyingGlass size={18} weight="bold" /> Áp dụng lọc
            </button>
          </div>
        ) : null}

        {preview.mode === "images" ? (
          <div className="history-delete-image-note">
            <div>
              <span className="eyebrow">TELEGRAM · IMAGE ONLY</span>
              <b>Chỉ chọn ảnh, không gồm video hoặc tài liệu</b>
              <small>
                Chỉ post Telegram đã được đánh dấu là <code>photo</code> hoặc <code>image/*</code>
                mới hiện ở đây. Nếu lịch sử cũ chưa có kết quả, hãy quét lại toàn bộ lịch sử một lần.
              </small>
            </div>
          </div>
        ) : null}

        <div className="history-delete-selectbar">
          <label>
            <input
              type="checkbox"
              checked={pageFullySelected}
              disabled={!items.length || loading}
              onChange={(event) => togglePage(event.target.checked)}
            />
            Chọn {items.length} post của trang này
          </label>
          <div>
            <b>{formatNumber(selectedCount)} / {formatNumber(total)} đã chọn</b>
            {preview.selectAll ? (
              <button className="button button--small button--outline" onClick={clearSelection}>
                Bỏ chọn tất cả
              </button>
            ) : (
              <button
                className="button button--small button--outline"
                disabled={!total}
                onClick={selectAll}
              >
                Chọn tất cả {formatNumber(total)}
              </button>
            )}
          </div>
        </div>

        {preview.mode !== "images" ? (
        <div className="history-delete-ai-filter">
          <div>
            <span className="eyebrow">CHATGPT · GỢI Ý LỌC NỘI DUNG</span>
            <b>Bạn muốn AI tìm và đề xuất xóa nội dung nào?</b>
            <small>
              Chỉ gửi tối đa 25 post đang hiển thị cho OpenAI trực tiếp; kết quả không tự xóa
              và không ghi prompt hay nội dung phân tích xuống SQLite.
            </small>
          </div>
          <textarea
            value={preview.aiInstruction}
            maxLength={1_000}
            onChange={(event) =>
              onChange((current) => ({ ...current, aiInstruction: event.target.value }))
            }
            placeholder="Ví dụ: Tìm các bài quảng cáo sàn, airdrop hoặc mời tham gia group khác."
          />
          <button
            className="button button--primary"
            disabled={saving || loading || items.length === 0 || preview.aiInstruction.trim().length < 3}
            onClick={() => onAnalyze(items)}
          >
            <Brain size={18} weight="bold" />
            {saving ? "Đang phân tích…" : `Dùng ChatGPT phân tích ${Math.min(items.length, 25)} post`}
          </button>
        </div>
        ) : null}

        <div className="history-delete-list">
          {loading && !candidates ? <LoadingState label="Đang tìm post phù hợp…" /> : null}
          {error ? <ErrorState error={error} onRetry={onRetry} /> : null}
          {!loading && !error && !items.length ? (
            <EmptyState
              title="Không có post phù hợp"
              description="Có thể các post đã bị xóa hoặc chưa có lịch sử được đồng bộ."
            />
          ) : null}
          {items.map((item) => (
            <article className={`telegram-delete-post ${isSelected(item.message_id) ? "is-selected" : ""}`} key={item.message_id}>
              <label className="telegram-delete-check">
                <input
                  type="checkbox"
                  checked={isSelected(item.message_id)}
                  onChange={(event) => setMessageSelected(item.message_id, event.target.checked)}
                  aria-label={`Chọn post ${item.message_id}`}
                />
              </label>
              <div className="telegram-post-bubble">
                <header>
                  <b>
                    {item.sender?.display_name
                      ? `${item.sender.display_name}${item.sender.username ? ` · @${item.sender.username.replace(/^@/, "")}` : ""}`
                      : item.sender_id
                        ? `Thành viên · ${item.sender_id}`
                        : "Channel / admin"}
                  </b>
                  <span>{formatRelative(item.sent_at)}</span>
                </header>
                {item.has_image ? (
                  <div className="telegram-image-indicator">ẢNH TELEGRAM · MỞ LINK ĐỂ XEM</div>
                ) : null}
                {item.text ? <p>{item.text}</p> : null}
                <footer>
                  <span>#{item.message_id}</span>
                  {item.has_image ? <Badge tone="teal">ẢNH</Badge> : null}
                  {item.reasons?.map((reason) => <Badge tone="yellow" key={reason}>{humanize(reason)}</Badge>)}
                  {item.matched_terms?.map((term) => <Badge tone="teal" key={term}>{term}</Badge>)}
                  {preview.aiResults?.[item.message_id] ? (
                    <span className={`ai-delete-result ${preview.aiResults[item.message_id].match ? "is-match" : ""}`}>
                      AI {preview.aiResults[item.message_id].match ? "ĐỀ XUẤT" : "KHÔNG KHỚP"} · {preview.aiResults[item.message_id].confidence}%
                      {preview.aiResults[item.message_id].reason ? ` · ${preview.aiResults[item.message_id].reason}` : ""}
                    </span>
                  ) : null}
                  {item.telegram_url ? (
                    <a href={item.telegram_url} target="_blank" rel="noreferrer">Mở trên Telegram</a>
                  ) : null}
                </footer>
              </div>
            </article>
          ))}
        </div>

        <footer className="history-delete-modal-footer">
          <div className="history-delete-pagination">
            <button
              className="button button--small button--outline"
              disabled={page <= 1 || loading}
              onClick={() => onChange((current) => ({ ...current, page: page - 1 }))}
            >
              <CaretLeft size={17} /> Trước
            </button>
            <span>Trang {page} / {pageCount}</span>
            <button
              className="button button--small button--outline"
              disabled={page >= pageCount || loading}
              onClick={() => onChange((current) => ({ ...current, page: page + 1 }))}
            >
              Sau <CaretRight size={17} />
            </button>
          </div>
          <button
            className="button button--danger"
            disabled={!selectedCount || saving}
            onClick={onDelete}
          >
            <Trash size={18} weight="bold" />
            {saving ? "Đang tạo yêu cầu…" : `Hãy xóa ${formatNumber(selectedCount)} post đã chọn`}
          </button>
        </footer>
      </section>
    </div>
  );
}

export function GroupDetailView({
  chatId,
  refreshKey,
  onBack,
  onCreatedAction,
  onToast,
}) {
  const resource = useResource(() => api.group(chatId), [chatId], refreshKey);
  const backfillResource = useResource(
    () => api.historyBackfillJobs(chatId),
    [chatId],
    refreshKey,
  );
  const deletionResource = useResource(
    () => api.historyLinkDeleteJobs(chatId),
    [chatId],
    refreshKey,
  );
  const group = resource.data;
  const [aiRoute, setAiRoute] = useState(null);
  const [aiEfficiency, setAiEfficiency] = useState(null);
  const [limits, setLimits] = useState(null);
  const [saving, setSaving] = useState("");
  const [acceptedPolicy, setAcceptedPolicy] = useState(null);
  const [writeError, setWriteError] = useState(null);
  const [deleteScope, setDeleteScope] = useState("vectors_only");
  const [deleteConfirmation, setDeleteConfirmation] = useState("");
  const [coverage, setCoverage] = useState(null);
  const [historySearchTerms, setHistorySearchTerms] = useState("");
  const [promotionOnly, setPromotionOnly] = useState(false);
  const [onlyMatches, setOnlyMatches] = useState(false);
  const [historyDeletePreview, setHistoryDeletePreview] = useState(null);
  const historyDeleteTriggerRef = useRef(null);
  const candidatesResource = useResource(
    () =>
      historyDeletePreview
        ? api.historyLinkDeleteCandidates(chatId, {
            mode: historyDeletePreview.mode,
            keywordTerms: historyDeletePreview.appliedKeywordTerms,
            senderQuery: historyDeletePreview.appliedSenderQuery,
            page: historyDeletePreview.page,
          })
        : Promise.resolve(null),
    [
      chatId,
      historyDeletePreview?.mode,
      historyDeletePreview?.appliedKeywordTerms,
      historyDeletePreview?.appliedSenderQuery,
      historyDeletePreview?.page,
    ],
    refreshKey,
  );

  useEffect(() => {
    if (!group) return;
    const policy = acceptedPolicy?.source === group ? acceptedPolicy.policy : group.policy;
    setAiRoute({
      mode: policy.ai_mode,
      preferred_cloud_provider: policy.preferred_cloud_provider || "openai",
      cloud_fallback: Boolean(policy.cloud_fallback),
    });
    setAiEfficiency({
      preset: policy.ai_efficiency_preset || "",
      filtering_level: policy.filtering_level || "standard",
      rag_top_k: policy.rag_top_k ?? "",
      rag_max_context_tokens: policy.rag_max_context_tokens ?? "",
    });
    setLimits({
      retention_days: policy.retention_days ?? "",
      max_messages: policy.max_messages ?? "",
      max_storage_mb: policy.max_storage_mb ?? "",
      max_vectors: policy.max_vectors ?? "",
    });
  }, [group, acceptedPolicy]);

  const perform = async (label, work, success) => {
    setSaving(label);
    setWriteError(null);
    try {
      const result = await work();
      if (["ai-route", "limits", "ai-efficiency"].includes(label)) setAcceptedPolicy({ source: group, policy: result });
      onToast(success);
      await resource.reload();
      return result;
    } catch (error) {
      setWriteError({ chatId, error });
      onToast(error.message, "error");
      return null;
    } finally {
      setSaving("");
    }
  };

  const createAction = async (action_type, payload, preview, reason) => {
    const action = await perform(
      action_type,
      () =>
        api.createGroupAction(chatId, {
          action_type,
          payload,
          preview,
          reason,
        }),
      "Đã tạo preview và chuyển sang bước owner xác nhận.",
    );
    if (action) onCreatedAction(action);
  };

  const createPermissionAction = async (permission, enabled) => {
    const action = await perform(
      `permission-${permission}`,
      () => api.setPermission(chatId, permission, enabled),
      "Đã tạo thay đổi quyền và chờ owner xác nhận.",
    );
    if (action) onCreatedAction(action);
  };

  const saveRoute = () =>
    perform(
      "ai-route",
      () => api.setAiRoute(chatId, aiRoute),
      "Đã cập nhật AI mode cho nguồn.",
    );

  const saveLimits = () => {
    const body = Object.fromEntries(
      Object.entries(limits).map(([key, value]) => [
        key,
        value === "" ? null : Number(value),
      ]),
    );
    return perform(
      "limits",
      () => api.setLimits(chatId, body),
      "Đã cập nhật retention và quota.",
    );
  };

  const saveAiEfficiency = () => {
    const body = {
      preset: aiEfficiency.preset || null,
      filtering_level: aiEfficiency.filtering_level,
      rag_top_k:
        aiEfficiency.rag_top_k === "" ? null : Number(aiEfficiency.rag_top_k),
      rag_max_context_tokens:
        aiEfficiency.rag_max_context_tokens === ""
          ? null
          : Number(aiEfficiency.rag_max_context_tokens),
    };
    return perform(
      "ai-efficiency",
      () => api.setAiEfficiency(chatId, body),
      "Đã cập nhật mức lọc và RAG budget của nguồn.",
    );
  };

  const previewLeave = async () => {
    const action = await perform(
      "leave-group",
      () =>
        api.previewLeaveGroup(chatId, {
          data_action: "keep",
          acknowledge_admin: Boolean(group.account_rights?.is_admin),
        }),
      "Đã tạo preview rời nguồn; dữ liệu cũ mặc định được giữ.",
    );
    if (action) onCreatedAction(action);
  };

  const previewKnowledgeDelete = async () => {
    const action = await perform(
      "delete-knowledge",
      () =>
        api.previewDeleteKnowledge(
          chatId,
          deleteScope,
          deleteScope === "all" ? deleteConfirmation : null,
        ),
      "Đã tạo preview xóa dữ liệu; chưa có dữ liệu nào bị xóa.",
    );
    if (action) onCreatedAction(action);
  };

  const checkCoverage = async () => {
    const report = await perform(
      "coverage-check",
      () => api.checkCoverage(chatId),
      "Đã đối chiếu SQLite và Local-first index.",
    );
    if (report) setCoverage(report);
  };

  const previewRecovery = async () => {
    const result = await perform(
      "recovery-preview",
      () => api.previewRecovery(chatId),
      "Đã tạo preview recovery; chưa reindex hay ghi vector.",
    );
    if (result?.pending_action) onCreatedAction(result.pending_action);
    if (result?.coverage) setCoverage(result.coverage);
  };

  const downloadHistory = async () => {
    if (onlyMatches && !historySearchTerms.trim()) {
      onToast("Nhập từ khóa trước khi chỉ xuất nội dung khớp.", "error");
      return;
    }
    setSaving("history-export");
    try {
      const blob = await api.exportGroupHistory(chatId, {
        searchTerms: historySearchTerms,
        promotionOnly,
        onlyMatches,
      });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `telegram-history-${group.title || chatId}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      onToast("Đã xuất lịch sử nguồn thành CSV.");
    } catch (error) {
      onToast(error.message, "error");
    } finally {
      setSaving("");
    }
  };

  const openHistoryLinkPreview = (event, mode) => {
    historyDeleteTriggerRef.current = event.currentTarget;
    setHistoryDeletePreview({
      mode,
      page: 1,
      selectAll: false,
      selected: [],
      excluded: [],
      aiInstruction: "",
      aiResults: {},
      keywordTerms: "",
      appliedKeywordTerms: "",
      senderQuery: "",
      appliedSenderQuery: "",
    });
  };

  const previewHistoryLinkDeletion = async () => {
    if (!historyDeletePreview) return;
    const { mode, selectAll, selected, excluded } = historyDeletePreview;
    const action = await perform(
      `history-delete-${mode}`,
      () =>
        api.previewHistoryLinkDeletion(chatId, {
          mode,
          keyword_terms: historyDeletePreview.appliedKeywordTerms,
          sender_query: historyDeletePreview.appliedSenderQuery,
          select_all: selectAll,
          selected_message_ids: selected,
          excluded_message_ids: excluded,
        }),
      "Đã tạo yêu cầu xóa; chưa có post Telegram nào bị xóa.",
    );
    if (action) {
      onCreatedAction(action);
      setHistoryDeletePreview(null);
    }
  };

  const analyzeHistoryDeleteCandidatesWithAi = async (items) => {
    if (!historyDeletePreview) return;
    const instruction = historyDeletePreview.aiInstruction.trim();
    if (instruction.length < 3) {
      onToast("Hãy mô tả nội dung AI cần tìm, tối thiểu 3 ký tự.", "error");
      return;
    }
    const messageIds = items.slice(0, 25).map((item) => item.message_id);
    if (!messageIds.length) return;
    setSaving("history-ai-filter");
    try {
      const response = await api.filterHistoryDeleteCandidatesWithAi(chatId, {
        mode: historyDeletePreview.mode,
        keyword_terms: historyDeletePreview.appliedKeywordTerms,
        sender_query: historyDeletePreview.appliedSenderQuery,
        instruction,
        message_ids: messageIds,
      });
      const aiResults = Object.fromEntries(
        (response.items || []).map((item) => [item.message_id, item]),
      );
      const matchedIds = (response.items || [])
        .filter((item) => item.match)
        .map((item) => item.message_id);
      setHistoryDeletePreview((current) => {
        if (!current) return current;
        return {
          ...current,
          aiResults: { ...current.aiResults, ...aiResults },
          selected: current.selectAll
            ? current.selected
            : [...new Set([...current.selected, ...matchedIds])],
          excluded: current.selectAll
            ? current.excluded.filter((id) => !matchedIds.includes(id))
            : current.excluded,
        };
      });
      onToast(`ChatGPT đã phân tích ${response.analyzed_count} post và tick các đề xuất.`);
    } catch (error) {
      onToast(error.message, "error");
    } finally {
      setSaving("");
    }
  };

  if (resource.loading && !group) return <LoadingState label="Đang tải policy nguồn…" />;
  if (resource.error && !group)
    return <ErrorState error={resource.error} onRetry={resource.reload} />;
  if (!group) return null;

  const permissions = group.permissions || {};
  const policy = acceptedPolicy?.source === group ? acceptedPolicy.policy : group.policy;
  const routeDirty = aiRoute && (aiRoute.mode !== policy.ai_mode || aiRoute.preferred_cloud_provider !== (policy.preferred_cloud_provider || "openai") || aiRoute.cloud_fallback !== Boolean(policy.cloud_fallback));
  const limitsDirty = limits && Object.entries(limits).some(([key, value]) => String(value) !== String(policy[key] ?? ""));
  const efficiencyDirty = aiEfficiency && Object.entries(aiEfficiency).some(([key, value]) => String(value) !== String(key === "preset" ? policy.ai_efficiency_preset || "" : key === "filtering_level" ? policy.filtering_level || "standard" : policy[key] ?? ""));
  const latestBackfill = backfillResource.data?.items?.[0] || null;
  const backfillPayload = latestBackfill?.payload || {};
  const latestDeletion = deletionResource.data?.items?.[0] || null;
  const deletionPayload = latestDeletion?.payload || {};

  return (
    <section className="ops-stack group-detail">
      {writeError?.chatId === chatId ? <p role="alert">{writeError.error.message} Biểu mẫu còn thay đổi sẽ được ghi rõ là bản nháp.</p> : null}
      {resource.error ? <ErrorState error={resource.error} onRetry={resource.reload} /> : null}
      <div className="ops-page-intro">
        <button className="button button--outline" onClick={onBack}>
          <ArrowLeft size={19} weight="bold" />
          Danh sách nhóm
        </button>
        <div>
          <p className="eyebrow">GROUP DETAIL · LIVE</p>
          <h2>{group.title || "Chưa có tên"}</h2>
          <span>
            {group.chat_id} · {group.chat_type} ·{" "}
            {group.username ? `@${group.username.replace(/^@/, "")}` : "không username"}
          </span>
        </div>
        <Badge tone={policy.allowed ? "success" : "magenta"}>
          {policy.allowed ? "ALLOW" : "BLOCK"}
        </Badge>
      </div>

      <section className="ops-two-column">
        <section className="panel">
          <PanelHeader
            eyebrow="GROUP STATUS"
            title="Trạng thái & quyền Telegram"
            action={<Badge tone="teal">LIVE</Badge>}
          />
          <div className="ops-setting-list">
            <PolicyAction
              title="Chat policy"
              description="BLOCK ngăn đọc, tìm kiếm, đồng bộ và xử lý nội dung."
              label="Chat ALLOW"
              active={policy.allowed}
              disabled={Boolean(saving)}
              onChange={(allowed) =>
                createAction(
                  "set_chat_allowed",
                  { allowed, memory_action: allowed ? "keep" : "archive" },
                  `${allowed ? "ALLOW" : "BLOCK"} ${group.title || group.chat_id}`,
                  "Thay đổi allowlist cần owner xác nhận.",
                )
              }
            />
            <div className="ops-setting-row">
              <div>
                <b>Quyền Telegram thực tế</b>
                <span>
                  {Object.entries(group.account_rights || {})
                    .filter(([, enabled]) => enabled)
                    .map(([name]) => humanize(name))
                    .join(" · ") || "Chưa phát hiện quyền"}
                </span>
              </div>
              <Badge tone="paper">{Object.keys(group.account_rights || {}).length} FLAGS</Badge>
            </div>
            <div className="ops-setting-row">
              <div>
                <b>Lần quan sát gần nhất</b>
                <span>{formatRelative(group.last_seen_at)}</span>
              </div>
              <Badge tone="paper">{group.chat_type.toUpperCase()}</Badge>
            </div>
          </div>
        </section>

        <section className="panel">
          <PanelHeader eyebrow="GROUP AI POLICY" title="AI mode & fallback" />
          <p>{routeDirty ? "Bản nháp chưa lưu" : "Cấu hình đã lưu"} · AI mode máy chủ: {policy.ai_mode}</p>
          {aiRoute ? (
            <div className="ops-form-body">
              <label className="ops-field">
                <span>AI mode</span>
                <select
                  value={aiRoute.mode}
                  onChange={(event) =>
                    setAiRoute((current) => ({ ...current, mode: event.target.value }))
                  }
                >
                  {AI_MODES.map(([value, label]) => (
                    <option value={value} key={value}>{label}</option>
                  ))}
                </select>
                <small className="field-helper">
                  Local là Ollama trên máy; cloud là OpenAI/OpenRouter; fallback là tự
                  chuyển sang lựa chọn còn lại khi tuyến ưu tiên không sẵn sàng.
                </small>
              </label>
              <label className="ops-field">
                <span>Cloud provider ưu tiên</span>
                <select
                  value={aiRoute.preferred_cloud_provider}
                  onChange={(event) =>
                    setAiRoute((current) => ({
                      ...current,
                      preferred_cloud_provider: event.target.value,
                    }))
                  }
                >
                  <option value="openai">OpenAI</option>
                  <option value="openrouter">OpenRouter</option>
                </select>
              </label>
              <PolicyAction
                title="Cloud fallback"
                description="Chỉ áp dụng cho local-first hoặc cloud-first."
                label="Cloud fallback"
                active={aiRoute.cloud_fallback}
                onChange={(cloud_fallback) =>
                  setAiRoute((current) => ({ ...current, cloud_fallback }))
                }
              />
              <button
                className="button button--primary"
                disabled={Boolean(saving) || !policy.allowed}
                onClick={saveRoute}
              >
                <ShieldCheck size={18} />
                Lưu AI route
              </button>
              {!policy.allowed ? (
                <small className="field-helper">Cần ALLOW nguồn trước khi đổi AI route.</small>
              ) : null}
            </div>
          ) : null}
        </section>
      </section>

      <section className="panel">
        <PanelHeader
          eyebrow="CHANNEL ARCHIVE · DỮ LIỆU"
          title="Lịch sử & xuất post"
          action={
            latestBackfill ? (
              <Badge tone={statusTone(latestBackfill.status)}>
                {humanize(latestBackfill.status)}
              </Badge>
            ) : (
              <Badge tone="yellow">CHƯA QUÉT TOÀN BỘ</Badge>
            )
          }
        />
        <div className="history-tools">
          <div className="history-tool-copy">
            <ClockCounterClockwise size={28} weight="bold" />
            <div>
              <b>Quét ngược đến bài đăng đầu tiên</b>
              <p>
                Worker đọc tối đa 500 post mỗi đợt, lưu checkpoint để có thể tiếp tục
                sau khi khởi động lại. Tin mới vẫn đồng bộ theo luồng riêng.
              </p>
            </div>
          </div>
          {latestBackfill ? (
            <div className="history-job-strip" aria-live="polite">
              <span>Đã lưu từ đợt quét</span>
              <b>{formatNumber(backfillPayload.processed || 0)} post</b>
              <small>{backfillPayload.progress_note || "Đang chờ worker cập nhật."}</small>
            </div>
          ) : null}
          <div className="ops-panel-actions">
            <button
              className="button button--outline"
              disabled={Boolean(saving) || !policy.allowed}
              onClick={() =>
                createAction(
                  "backfill_chat_history",
                  { batch_size: 500 },
                  `Quét toàn bộ lịch sử của ${group.title} từ post mới nhất về post đầu tiên; mỗi đợt tối đa 500 post.`,
                  "Chỉ đọc nguồn đã được cấp quyền. Tiến độ chạy nền, có checkpoint và không gửi nội dung sang AI.",
                )
              }
            >
              <ClockCounterClockwise size={18} />
              Quét toàn bộ lịch sử
            </button>
          </div>
        </div>
        <div className="history-export-form">
          <label className="ops-field">
            <span>Nội dung cần tìm</span>
            <input
              value={historySearchTerms}
              onChange={(event) => setHistorySearchTerms(event.target.value)}
              placeholder="Ví dụ: SOL, ETF, airdrop (ngăn cách bằng dấu phẩy)"
              maxLength={2000}
            />
            <small className="field-helper">
              CSV luôn đánh dấu post khớp từ khóa; có thể chỉ xuất các post đó.
            </small>
          </label>
          <label className="history-check">
            <input
              type="checkbox"
              checked={promotionOnly}
              onChange={(event) => setPromotionOnly(event.target.checked)}
            />
            <span>Chỉ xuất post có promotion/link</span>
          </label>
          <label className="history-check">
            <input
              type="checkbox"
              checked={onlyMatches}
              onChange={(event) => setOnlyMatches(event.target.checked)}
            />
            <span>Chỉ xuất nội dung khớp từ khóa</span>
          </label>
          <button
            className="button button--primary"
            disabled={Boolean(saving)}
            onClick={downloadHistory}
          >
            <DownloadSimple size={18} />
            {saving === "history-export" ? "Đang tạo CSV…" : "Xuất lịch sử CSV"}
          </button>
        </div>
        <div className="history-delete-zone">
          <div>
            <span className="eyebrow">DESTRUCTIVE · OWNER CONFIRMATION</span>
            <b>Xóa post lịch sử trên Telegram</b>
            <p>
              Chỉ dùng khi tài khoản có quyền <code>delete_messages</code>. Bước dưới đây
              chỉ tạo preview; post mới sau preview không bị xóa.
            </p>
          </div>
          {latestDeletion ? (
            <div className="history-delete-progress" aria-live="polite">
              <span>{humanize(latestDeletion.status)}</span>
              <b>{formatNumber(deletionPayload.deleted || 0)} post đã xóa</b>
              <small>{deletionPayload.progress_note || "Chờ worker cập nhật."}</small>
            </div>
          ) : null}
          <div className="history-delete-actions">
            <button
              className="button button--danger"
              disabled={Boolean(saving) || !policy.allowed || !permissions.delete_any_messages}
              onClick={(event) => openHistoryLinkPreview(event, "all_links")}
            >
              Preview: xóa mọi post có link
            </button>
            <button
              className="button button--danger"
              disabled={Boolean(saving) || !policy.allowed || !permissions.delete_any_messages}
              onClick={(event) => openHistoryLinkPreview(event, "promotion_links")}
            >
              Preview: xóa link + promotion
            </button>
            <button
              className="button button--danger"
              disabled={Boolean(saving) || !policy.allowed || !permissions.delete_any_messages}
              onClick={(event) => openHistoryLinkPreview(event, "keywords")}
            >
              Preview: xóa theo tên/từ khóa
            </button>
            <button
              className="button button--danger"
              disabled={Boolean(saving) || !policy.allowed || !permissions.delete_any_messages}
              onClick={(event) => openHistoryLinkPreview(event, "sender")}
            >
              Preview: xóa theo người đăng
            </button>
            <button
              className="button button--danger"
              disabled={Boolean(saving) || !policy.allowed || !permissions.delete_any_messages}
              onClick={(event) => openHistoryLinkPreview(event, "images")}
            >
              Preview: xóa post có ảnh
            </button>
          </div>
          {!permissions.delete_any_messages ? (
            <small className="field-helper">
              Hãy bật quyền <code>delete_any_messages</code> cho nguồn trước khi tạo preview.
            </small>
          ) : null}
        </div>
      </section>

      <HistoryLinkDeletePreviewModal
        preview={historyDeletePreview}
        candidates={candidatesResource.data}
        loading={candidatesResource.loading}
        error={candidatesResource.error}
        saving={Boolean(saving)}
        triggerRef={historyDeleteTriggerRef}
        onClose={() => setHistoryDeletePreview(null)}
        onRetry={candidatesResource.reload}
        onChange={setHistoryDeletePreview}
        onDelete={previewHistoryLinkDeletion}
        onAnalyze={analyzeHistoryDeleteCandidatesWithAi}
      />

      <section className="panel">
        <PanelHeader
          eyebrow="LOCAL-FIRST EFFICIENCY"
          title="Lọc nội dung & RAG budget"
          action={<Badge tone="teal">OLLAMA EMBEDDING</Badge>}
        />
        {aiEfficiency ? (
          <div className="ops-form-grid">
            <label className="ops-field">
              <span>Preset nguồn</span>
              <select
                value={aiEfficiency.preset}
                onChange={(event) =>
                  setAiEfficiency((current) => ({ ...current, preset: event.target.value }))
                }
              >
                <option value="">Theo cấu hình toàn cục</option>
                <option value="saving">Tiết kiệm · local ưu tiên</option>
                <option value="balanced">Cân bằng · đề xuất</option>
                <option value="quality">Chất lượng cao</option>
              </select>
            </label>
            <label className="ops-field">
              <span>Lọc trước embedding</span>
              <select
                value={aiEfficiency.filtering_level}
                onChange={(event) =>
                  setAiEfficiency((current) => ({
                    ...current,
                    filtering_level: event.target.value,
                  }))
                }
              >
                <option value="relaxed">Nới lỏng · giữ nhiều tin hơn</option>
                <option value="standard">Chuẩn · đề xuất</option>
                <option value="strict">Nghiêm · giảm vector/token local</option>
              </select>
            </label>
            <label className="ops-field">
              <span>RAG top_k</span>
              <input
                type="number"
                min="1"
                max="50"
                value={aiEfficiency.rag_top_k}
                onChange={(event) =>
                  setAiEfficiency((current) => ({ ...current, rag_top_k: event.target.value }))
                }
                placeholder="Theo preset"
              />
            </label>
            <label className="ops-field">
              <span>Context token tối đa</span>
              <input
                type="number"
                min="500"
                max="50000"
                value={aiEfficiency.rag_max_context_tokens}
                onChange={(event) =>
                  setAiEfficiency((current) => ({
                    ...current,
                    rag_max_context_tokens: event.target.value,
                  }))
                }
                placeholder="Theo preset"
              />
            </label>
            <div className="ops-local-warning">
              <Brain size={22} weight="fill" />
              <b>Không gửi toàn bộ lịch sử lên cloud</b>
              <span>
                Nội dung được lọc và embedding bằng Ollama; RAG chỉ gửi các đoạn liên quan
                trong giới hạn context đã chọn.
              </span>
            </div>
            <button
              className="button button--primary"
              disabled={Boolean(saving) || !policy.allowed}
              onClick={saveAiEfficiency}
            >
              <FloppyDisk size={18} />
              Lưu hiệu quả AI
            </button>
            <p>{efficiencyDirty ? "Bản nháp chưa lưu" : "Cấu hình đã lưu"} · Hiệu quả AI theo phản hồi máy chủ.</p>
          </div>
        ) : null}
      </section>

      <section className="panel">
        <PanelHeader
          eyebrow="PERMISSION MATRIX"
          title="Quyền dữ liệu"
          action={<Badge tone="yellow">19 PERMISSIONS</Badge>}
        />
        <div className="permission-matrix">
          {PERMISSION_GROUPS.map(([title, items]) => (
            <fieldset key={title}>
              <legend>{title}</legend>
              {items.map((permission) => (
                <div key={permission}>
                  <span className="permission-copy">
                    <code>{permission}</code>
                    <small>{PERMISSION_HELP[permission]}</small>
                  </span>
                  <Toggle
                    label={permission}
                    active={Boolean(permissions[permission])}
                    disabled={Boolean(saving)}
                    onChange={(enabled) => createPermissionAction(permission, enabled)}
                  />
                </div>
              ))}
            </fieldset>
          ))}
        </div>
      </section>

      <section className="ops-two-column">
        <section className="panel">
          <PanelHeader eyebrow="RETENTION & QUOTA" title="Giới hạn dữ liệu" />
          <p>{limitsDirty ? "Bản nháp chưa lưu" : "Cấu hình đã lưu"} · Retention máy chủ: {policy.retention_days ?? "Không giới hạn"} ngày.</p>
          {limits ? (
            <>
              <div className="quota-presets" role="group" aria-label="Preset quota">
                <button
                  style={{ minHeight: 44 }}
                  onClick={() =>
                    setLimits({
                      retention_days: 7,
                      max_messages: 10_000,
                      max_vectors: 10_000,
                      max_storage_mb: 256,
                    })
                  }
                >
                  Nhẹ
                </button>
                <button
                  style={{ minHeight: 44 }}
                  onClick={() =>
                    setLimits({
                      retention_days: 30,
                      max_messages: 50_000,
                      max_vectors: 50_000,
                      max_storage_mb: 1024,
                    })
                  }
                >
                  Lớn
                </button>
                <button
                  style={{ minHeight: 44 }}
                  onClick={() =>
                    setLimits({
                      retention_days: "",
                      max_messages: "",
                      max_vectors: "",
                      max_storage_mb: "",
                    })
                  }
                >
                  Không giới hạn
                </button>
              </div>
              <div className="quota-grid">
                {[
                  ["retention_days", "Số ngày giữ dữ liệu"],
                  ["max_messages", "Số tin tối đa"],
                  ["max_vectors", "Số vector tối đa"],
                  ["max_storage_mb", "Dung lượng tối đa (MB)"],
                ].map(([key, label]) => (
                  <label className="ops-field" key={key}>
                    <span>{label}</span>
                    <input
                      type="number"
                      min="1"
                      value={limits[key]}
                      placeholder="Không giới hạn"
                      onChange={(event) =>
                        setLimits((current) => ({
                          ...current,
                          [key]: event.target.value,
                        }))
                      }
                    />
                  </label>
                ))}
              </div>
              <p className="policy-footnote">
                Retention quyết định dữ liệu được giữ bao lâu. Quota giới hạn số tin,
                số embedding vector và dung lượng của riêng nguồn này.
              </p>
              <button
                className="button button--primary"
                disabled={Boolean(saving) || !policy.allowed}
                onClick={saveLimits}
              >
                <Database size={18} />
                Lưu retention & quota
              </button>
            </>
          ) : null}
        </section>

        <section className="panel">
          <PanelHeader eyebrow="SPECIAL FEATURES" title="Tính năng đặc biệt" />
          <div className="ops-setting-list">
            <PolicyAction
              title="Cho phép thành viên dùng @your_assistant_username /ask"
              description="Chỉ hoạt động khi nguồn ALLOW và quyền gửi tin hợp lệ."
              label="Group AI ask"
              active={group.group_ai_ask}
              disabled={Boolean(saving)}
              onChange={(enabled) =>
                createAction(
                  "set_group_ai_ask",
                  { enabled },
                  `${enabled ? "Bật" : "Tắt"} /ask trong ${group.title}`,
                  "Standing authorization cho AI trả lời trong group.",
                )
              }
            />
            <PolicyAction
              title="AUTO xóa link mới của non-admin"
              description="Owner/admin được miễn; rule chỉ xử lý tin mới."
              label="Auto xóa link non-admin"
              active={group.auto_link_moderation}
              disabled={Boolean(saving)}
              onChange={(enabled) =>
                createAction(
                  "set_link_spam_auto_moderation",
                  { enabled },
                  `${enabled ? "Bật" : "Tắt"} AUTO xóa link non-admin`,
                  "Thay đổi moderation tự động cần owner xem trước.",
                )
              }
            />
          </div>
          <div className="ops-panel-actions">
            <button
              className="button button--outline"
              disabled={Boolean(saving)}
              onClick={() =>
                createAction(
                  "sync_chat_history",
                  { limit: 1000 },
                  `Đồng bộ 1.000 tin mới nhất của ${group.title}`,
                  "Chỉ đọc nguồn đã được cấp quyền.",
                )
              }
            >
              <ArrowsClockwise size={18} />
              Đồng bộ
            </button>
            <button
              className="button button--primary"
              disabled={Boolean(saving)}
              onClick={() =>
                createAction(
                  "enable_group_learning",
                  { limit: 1000 },
                  `Bật học và lập chỉ mục ${group.title}`,
                  "Dữ liệu được đồng bộ vào SQLite trước khi embedding.",
                )
              }
            >
              <Brain size={18} />
              Học nguồn này
            </button>
          </div>
        </section>
      </section>

      <section className="panel">
        <PanelHeader
          eyebrow="KNOWLEDGE STATUS · LIVE"
          title="Dữ liệu đã học"
          action={
            <Badge tone={statusTone(group.knowledge.status)}>
              {learningLabel(group.knowledge.status)}
            </Badge>
          }
        />
        <div className="knowledge-status-grid">
          <div>
            <span>Tin trong SQLite</span>
            <b>{formatNumber(group.knowledge.mysql_message_count)}</b>
            <small>SOURCE OF TRUTH</small>
          </div>
          <div>
            <span>Vector</span>
            <b>{formatNumber(group.knowledge.vector_count)}</b>
            <small>DERIVED INDEX</small>
          </div>
          <div>
            <span>Dung lượng</span>
            <b>{formatBytes(group.knowledge.storage_bytes)}</b>
            <small>MESSAGE + MEDIA</small>
          </div>
          <div>
            <span>Lần học gần nhất</span>
            <b>{formatRelative(group.knowledge.last_learned_at)}</b>
            <small>{group.knowledge.last_job_id || "CHƯA CÓ JOB"}</small>
          </div>
        </div>
        {group.knowledge.last_error ? (
          <div className="ops-local-warning ops-local-warning--yellow">
            <Warning size={22} weight="fill" />
            <b>Lỗi gần nhất</b>
            <span>{group.knowledge.last_error}</span>
          </div>
        ) : null}
        <div className="ops-action-row">
          <button className="button button--outline" disabled={Boolean(saving)} onClick={checkCoverage}>
            <ArrowsClockwise size={18} />Kiểm tra coverage
          </button>
          {coverage?.coverage_state !== "healthy" ? (
            <button className="button button--outline" disabled={Boolean(saving)} onClick={previewRecovery}>
              <Warning size={18} />Preview khôi phục index
            </button>
          ) : null}
        </div>
        {coverage ? (
          <div className={`ops-local-warning ${coverage.coverage_state === "healthy" ? "ops-local-warning--green" : "ops-local-warning--yellow"}`}>
            <Database size={22} weight="fill" />
            <b>{coverage.coverage_state === "healthy" ? "Coverage Local-first đầy đủ" : "Cần đối chiếu index"}</b>
            <span>{formatNumber(coverage.active_vectors)} vector / {formatNumber(coverage.expected_eligible_messages)} tin eligible ({coverage.coverage_percent}%). Legacy: {coverage.legacy_vectors == null ? "không đọc được" : formatNumber(coverage.legacy_vectors)}.</span>
          </div>
        ) : null}
      </section>

      <section className="ops-danger-zone">
        <div>
          <Warning size={28} weight="fill" />
          <div>
            <b>BLOCK nguồn</b>
            <span>Preview → owner duyệt → worker kiểm tra lại → thực thi → audit.</span>
          </div>
        </div>
        <button
          className="button button--danger"
          disabled={Boolean(saving) || !policy.allowed}
          onClick={() =>
            createAction(
              "set_chat_allowed",
              { allowed: false, memory_action: "archive" },
              `BLOCK ${group.title} và archive memory liên quan`,
              "Nguồn sẽ không còn được đọc, tìm kiếm hoặc học tiếp.",
            )
          }
        >
          <LockKey size={18} weight="bold" />
          Preview chuyển BLOCK
        </button>
        <button
          className="button button--danger"
          disabled={Boolean(saving) || group.account_rights?.is_creator}
          onClick={previewLeave}
        >
          <SignOut size={18} weight="bold" />
          Preview rời {group.chat_type === "channel" ? "channel" : "group"}
        </button>
      </section>
      <section className="ops-danger-zone ops-danger-zone--large">
        <div>
          <Warning size={28} weight="fill" />
          <div>
            <b>Xóa dữ liệu đã học</b>
            <span>Chọn phạm vi → xem số tin/vector/tệp → owner xác nhận.</span>
          </div>
        </div>
        <select
          className="ops-danger-select"
          aria-label="Phạm vi xóa dữ liệu đã học"
          style={{ minHeight: 44 }}
          value={deleteScope}
          onChange={(event) => setDeleteScope(event.target.value)}
        >
          <option value="vectors_only">Chỉ vector</option>
          <option value="search_index">Chỉ mục tìm kiếm + checkpoint</option>
          <option value="mysql_content">Nội dung SQLite + index</option>
          <option value="media_only">Chỉ media local</option>
          <option value="reset_checkpoint">Chỉ reset checkpoint</option>
          <option value="all">Toàn bộ dữ liệu nguồn</option>
        </select>
        {deleteScope === "all" ? (
          <label className="ops-field danger-confirm-field">
            <span>Nhập đúng tên nguồn để mở khóa preview</span>
            <input
              value={deleteConfirmation}
              onChange={(event) => setDeleteConfirmation(event.target.value)}
              placeholder={group.title || group.chat_id}
            />
          </label>
        ) : null}
        <button
          className="button button--danger"
          disabled={
            Boolean(saving) ||
            (deleteScope === "all" && deleteConfirmation !== (group.title || group.chat_id))
          }
          onClick={previewKnowledgeDelete}
        >
          <Warning size={18} weight="bold" />
          Preview xóa dữ liệu
        </button>
      </section>
    </section>
  );
}
