import { useMemo, useRef, useState } from "react";
import {
  ArrowsOut,
  Brain,
  CheckCircle,
  Clock,
  Cloud,
  Copy,
  Database,
  FileText,
  Key,
  LockKey,
  MagnifyingGlass,
  Robot,
  ShieldCheck,
  Sparkle,
  Warning,
  X,
} from "@phosphor-icons/react";

import { api } from "../api.js";
import { formatBytes, formatDate, formatNumber } from "../format.js";
import { useResource } from "../hooks.js";
import { useDialogA11y } from "../useDialogA11y.js";
import {
  Badge,
  ErrorState,
  LoadingState,
  PanelHeader,
} from "../ui.jsx";

export function ConnectionsView({
  refreshKey,
  realtimeState,
  lastRealtimeAt,
  reconnectAttempt,
  onRetry,
}) {
  const overview = useResource(api.overview, [], refreshKey);
  const ai = useResource(api.aiConfig, [], refreshKey);
  const workers = useResource(api.workers, [], refreshKey);

  if (overview.loading && !overview.data && realtimeState === "live")
    return <LoadingState label="Đang kiểm tra kết nối…" />;
  if (overview.error && !overview.data && realtimeState === "live")
    return <ErrorState error={overview.error} onRetry={overview.reload} />;

  const metric = workers.data?.latest_metric;
  const isLive = realtimeState === "live";
  const isOffline = realtimeState === "offline";
  const realtimeLabel = {
    connecting: "CONNECTING",
    live: "LIVE",
    reconnecting: "RECONNECTING",
    offline: "OFFLINE",
    session_expired: "SESSION EXPIRED",
  }[realtimeState] || "UNKNOWN";
  const realtimeTone = isLive ? "success" : isOffline ? "magenta" : "yellow";
  const rows = [
    [
      "Admin API",
      "Loopback owner console",
      overview.error ? "UNAVAILABLE" : "ONLINE",
      overview.error ? "magenta" : "success",
      "127.0.0.1:8765",
    ],
    [
      "MySQL",
      "Source of truth",
      overview.error ? "UNKNOWN" : "ONLINE",
      overview.error ? "yellow" : "success",
      overview.data ? `${formatNumber(overview.data.joined_sources)} nguồn` : "Snapshot chưa có",
    ],
    ["Qdrant", "Derived vector index", metric ? "ONLINE" : "WAITING", metric ? "success" : "yellow", formatBytes(metric?.vector_bytes)],
    ["AI provider", ai.data?.model || "AI đang tắt", String(ai.data?.provider || "off").toUpperCase(), ai.data?.provider === "off" ? "yellow" : "success", ai.data?.embedding_model || "—"],
    [
      "SSE realtime",
      "Dashboard snapshot stream",
      realtimeLabel,
      realtimeTone,
      lastRealtimeAt ? `Lần thành công cuối: ${formatDate(lastRealtimeAt)}` : "Chưa có snapshot",
    ],
  ];

  return (
    <section className="ops-stack">
      <section className={`connection-health connection-health--${realtimeState}`}>
        <div>
          {isLive ? (
            <CheckCircle size={28} weight="fill" />
          ) : (
            <Warning size={28} weight="fill" />
          )}
          <div>
            <b>
              {isLive
                ? "Admin API và SSE đang hoạt động"
                : isOffline
                  ? "Dashboard đang offline"
                  : realtimeState === "connecting"
                    ? "Đang kết nối Admin API và SSE"
                    : "SSE đang kết nối lại"}
            </b>
            <span>
              {isLive
                ? "Snapshot mới có thể cập nhật các chỉ số vận hành."
                : `Giữ snapshot gần nhất${
                    lastRealtimeAt ? ` từ ${formatDate(lastRealtimeAt)}` : ""
                  }; thao tác ghi cần kiểm tra lại kết nối.`}
            </span>
          </div>
        </div>
        <div className="connection-health-actions">
          <Badge tone={realtimeTone}>{realtimeLabel}</Badge>
          {!isLive ? (
            <button className="button button--outline" onClick={onRetry}>
              Thử lại{reconnectAttempt ? ` · ${reconnectAttempt}` : ""}
            </button>
          ) : null}
        </div>
      </section>
      <section className="connection-grid">
        {rows.map(([name, description, state, tone, detail]) => (
          <article className="connection-card" key={name}>
            <div className="connection-card-top">
              <span className={`service-light service-light--${tone === "success" ? "online" : "warning"}`} />
              <div><h3>{name}</h3><span>{description}</span></div>
              <Badge tone={tone}>{state}</Badge>
            </div>
            <div className="connection-fields">
              <label><span>Trạng thái runtime</span><input value={detail} readOnly /></label>
              <label><span>Bảo mật</span><input value="Secret không hiển thị" readOnly /></label>
            </div>
          </article>
        ))}
      </section>
    </section>
  );
}

export function PolicyView() {
  const rules = [
    ["01", "System safety", "GLOBAL", "Secret scanner, owner-only auth và loopback boundary."],
    ["02", "Group allowlist", "DEFAULT DENY", "Nguồn BLOCK không được đọc, đồng bộ, tìm kiếm hoặc học."],
    ["03", "Permission matrix", "19 FLAGS", "Mỗi chức năng cần đúng quyền con trước khi thực thi."],
    ["04", "AI routing", "PER GROUP", "Inherit, local-only, local-first, cloud-only, cloud-first hoặc off."],
    ["05", "Destructive actions", "PENDING", "Preview, owner xác nhận, worker kiểm tra lại, audit."],
    ["06", "AUTO moderation", "STANDING AUTH", "Chỉ xử lý link mới của non-admin sau khi owner bật rule."],
  ];
  return (
    <section className="ops-two-column ops-policy-layout">
      <section className="panel">
        <PanelHeader eyebrow="DECISION STACK" title="Các lớp policy" action={<Badge tone="teal">LIVE RULES</Badge>} />
        <div className="ops-rule-list">
          {rules.map(([index, name, decision, detail]) => (
            <div className="ops-rule" key={name}>
              <span>{index}</span>
              <div><b>{name}</b><small>{detail}</small></div>
              <Badge tone={decision === "DEFAULT DENY" ? "magenta" : decision === "PENDING" ? "yellow" : "teal"}>{decision}</Badge>
            </div>
          ))}
        </div>
      </section>
      <section className="panel ops-simulator">
        <PanelHeader eyebrow="ENFORCEMENT FLOW" title="Quy trình quyết định" action={<ShieldCheck size={25} weight="fill" />} />
        <div className="ops-flow ops-flow--vertical">
          <span>Owner session</span><b>↓</b>
          <span>Chat allowlist</span><b>↓</b>
          <span>Permission flag</span><b>↓</b>
          <span>Telegram rights</span><b>↓</b>
          <span>Pending confirmation</span><b>↓</b>
          <span>Execute + Audit</span>
        </div>
        <p className="policy-footnote">
          Dashboard không tự suy diễn quyền. Mọi quyết định cuối cùng vẫn được backend và worker
          kiểm tra lại trước khi tương tác với Telegram.
        </p>
      </section>
    </section>
  );
}

export function SecurityView() {
  return (
    <section className="ops-stack">
      <section className="ops-security-hero">
        <ShieldCheck size={56} weight="fill" />
        <div>
          <p className="eyebrow">SECURITY POSTURE · ACTIVE</p>
          <h2>Không thực thi nếu chưa chắc chắn.</h2>
          <span>Fail closed cho dữ liệu; fail safe cho tin nhắn Telegram.</span>
        </div>
        <Badge tone="success">LOCAL</Badge>
      </section>
      <section className="ops-two-column">
        <section className="panel">
          <PanelHeader eyebrow="OWNER SESSION" title="Xác thực dashboard" />
          <div className="ops-setting-list">
            <div className="ops-setting-row"><div><b>Mã đăng nhập 8 số</b><span>HMAC, hết hạn sau 5 phút.</span></div><Badge tone="success">ACTIVE</Badge></div>
            <div className="ops-setting-row"><div><b>Cookie HttpOnly</b><span>JavaScript không đọc được session token.</span></div><Badge tone="success">ACTIVE</Badge></div>
            <div className="ops-setting-row"><div><b>CSRF header</b><span>Mọi request ghi cần X-CSRF-Token.</span></div><Badge tone="success">ACTIVE</Badge></div>
            <div className="ops-setting-row"><div><b>Loopback only</b><span>Admin API không lắng nghe mạng LAN.</span></div><Badge tone="success">127.0.0.1</Badge></div>
          </div>
        </section>
        <section className="panel">
          <PanelHeader eyebrow="SECRET BOUNDARY" title="Dữ liệu không rời backend" />
          <div className="ops-flow">
            <span>Telegram</span><b>→</b><span>Policy</span><b>→</b><span>AI Router</span><b>→</b><span>Audit</span>
          </div>
          <div className="ops-local-warning">
            <LockKey size={22} weight="fill" />
            <b>Write-only secrets</b>
            <span>API key, token và mật khẩu không có endpoint đọc và không render trên UI.</span>
          </div>
          <div className="ops-local-warning ops-local-warning--yellow">
            <Warning size={22} weight="fill" />
            <b>Destructive guard</b>
            <span>Xóa model và cleanup storage bắt buộc đi qua preview + PendingAction.</span>
          </div>
        </section>
      </section>
    </section>
  );
}

export function TelegramFeaturesView() {
  const features = [
    ["Hỏi AI", "Dùng /ask hoặc nhắc @your_assistant_username trong group được cấp quyền.", Sparkle],
    ["Tìm kiếm Telegram", "Tìm message đã đồng bộ trong MySQL theo policy từng group.", Database],
    ["Tổng hợp 7 ngày", "Tổng hợp tin mới, loại trùng và giữ dẫn chứng nguồn.", Clock],
    ["Tra giá CoinGecko", "Kiểm tra giá mà không phụ thuộc AI provider.", Cloud],
    ["Tasks & reminders", "Tạo và nhận nhắc việc ngay trong Telegram bot.", CheckCircle],
    ["Memory", "Lưu ghi chú owner; secret không được đưa vào memory.", Brain],
    ["Quản lý group", "ALLOW/BLOCK, permission, AI mode, quota và retention.", ShieldCheck],
    ["Learning sources", "Đồng bộ MySQL, embedding và theo dõi learning job.", FileText],
    ["PendingAction", "Owner duyệt trước khi worker thực thi thao tác nhạy cảm.", Key],
  ];
  return (
    <section className="ops-stack">
      <section className="telegram-feature-intro">
        <Robot size={54} weight="fill" />
        <div>
          <p className="eyebrow">PRIMARY INTERFACE</p>
          <h2>Telegram Assistant</h2>
          <span>Dashboard là bảng vận hành phụ; tương tác trợ lý hằng ngày vẫn diễn ra trong Telegram.</span>
        </div>
        <Badge tone="success">RUNTIME ACTIVE</Badge>
      </section>
      <section className="telegram-feature-list">
        {features.map(([title, description, Icon], index) => (
          <article key={title}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <Icon size={27} weight="bold" />
            <div><h3>{title}</h3><p>{description}</p></div>
            <Badge tone="teal">AVAILABLE</Badge>
          </article>
        ))}
      </section>
    </section>
  );
}

export function DocumentationView({ refreshKey }) {
  const overview = useResource(api.overview, [], refreshKey);
  const workers = useResource(api.workers, [], refreshKey);
  const docs = useResource(api.documents, [], refreshKey);
  const [reader, setReader] = useState(null);
  const [readerError, setReaderError] = useState(null);
  const [docSearch, setDocSearch] = useState("");
  const [expanded, setExpanded] = useState(false);
  const documentTriggerRef = useRef(null);
  const dialogRef = useDialogA11y(
    Boolean(reader),
    () => setReader(null),
    documentTriggerRef,
  );
  const visibleContent = useMemo(() => {
    if (!reader?.content || !docSearch.trim()) return reader?.content || "";
    const needle = docSearch.trim().toLocaleLowerCase("vi");
    return reader.content
      .split("\n")
      .filter((line) => line.toLocaleLowerCase("vi").includes(needle))
      .join("\n");
  }, [reader, docSearch]);

  const openDocument = async (document) => {
    setReaderError(null);
    setReader({ ...document, content: "", loading: true });
    try {
      setReader(await api.document(document.id));
    } catch (error) {
      setReaderError(error);
    }
  };
  return (
    <section className="ops-stack">
      <section className="ops-resource-strip">
        <div><Robot size={30} /><span>Package</span><b>v0.1.0</b><Badge tone="success">CURRENT</Badge></div>
        <div><CheckCircle size={30} /><span>Admin API</span><b>{overview.data ? "ONLINE" : "CHECKING"}</b><Badge tone={overview.data ? "success" : "yellow"}>LIVE</Badge></div>
        <div><Clock size={30} /><span>Scheduler</span><b>{workers.data?.scheduler?.length || 0} jobs</b><Badge tone="teal">ACTIVE</Badge></div>
      </section>
      <section className="integration-readiness integration-readiness--live">
        <div>
          <p className="eyebrow">INTEGRATION STATUS</p>
          <h2>Frontend và Admin API đã kết nối</h2>
          <span>Owner auth, cookie HttpOnly, CSRF, SSE và dữ liệu MySQL đang hoạt động cùng origin.</span>
        </div>
        <Badge tone="success">READY</Badge>
      </section>
      <section className="panel">
        <PanelHeader eyebrow="LOCAL DOCUMENTATION" title="Tài liệu hệ thống" />
        <div className="ops-doc-list">
          {(docs.data?.items || []).map((document) => (
            <button
              type="button"
              key={document.id}
              disabled={!document.available}
              onClick={(event) => {
                documentTriggerRef.current = event.currentTarget;
                openDocument(document);
              }}
            >
              <FileText size={24} />
              <div><b>{document.name}</b><span>{document.description}</span></div>
              <Badge tone={document.available ? "paper" : "yellow"}>
                {document.available ? "MỞ ĐỌC" : "CHƯA CÓ"}
              </Badge>
            </button>
          ))}
        </div>
        {docs.loading && !docs.data ? <LoadingState label="Đang đọc danh mục tài liệu…" /> : null}
        {docs.error ? <ErrorState error={docs.error} onRetry={docs.reload} /> : null}
      </section>
      <section className="panel">
        <PanelHeader eyebrow="CURRENT SCOPE" title="Thông tin runtime" />
        {overview.data ? (
          <div className="knowledge-status-grid">
            <div><span>Nguồn Telegram</span><b>{formatNumber(overview.data.joined_sources)}</b><small>MYSQL</small></div>
            <div><span>AI provider</span><b>{String(overview.data.ai_provider).toUpperCase()}</b><small>{overview.data.ai_model}</small></div>
            <div><span>Pending action</span><b>{formatNumber(overview.data.pending_actions)}</b><small>OWNER REVIEW</small></div>
            <div><span>RAM runtime</span><b>{formatBytes(overview.data.runtime?.rss_bytes)}</b><small>{formatDate(overview.data.runtime?.collected_at)}</small></div>
          </div>
        ) : overview.error ? (
          <ErrorState error={overview.error} onRetry={overview.reload} />
        ) : (
          <LoadingState />
        )}
      </section>
      {reader ? (
        <div className="modal-backdrop doc-reader-backdrop" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setReader(null);
        }}>
          <article
            ref={dialogRef}
            className={`doc-reader ${expanded ? "is-expanded" : ""}`}
            role="dialog"
            aria-modal="true"
            aria-labelledby="doc-reader-title"
            tabIndex={-1}
          >
            <header>
              <div>
                <p className="eyebrow">LOCAL DOCUMENT · READ ONLY</p>
                <h2 id="doc-reader-title">{reader.name}</h2>
              </div>
              <div className="row-actions">
                <button
                  className="icon-button"
                  aria-label="Sao chép tài liệu"
                  onClick={() => navigator.clipboard.writeText(reader.content || "")}
                  disabled={!reader.content}
                >
                  <Copy size={20} />
                </button>
                <button className="icon-button" aria-label="Mở rộng trình đọc" onClick={() => setExpanded((value) => !value)}>
                  <ArrowsOut size={20} />
                </button>
                <button className="icon-button" aria-label="Đóng trình đọc" onClick={() => setReader(null)}>
                  <X size={22} />
                </button>
              </div>
            </header>
            <label className="search-field doc-search">
              <MagnifyingGlass size={20} />
              <input value={docSearch} onChange={(event) => setDocSearch(event.target.value)} placeholder="Tìm trong tài liệu…" />
            </label>
            {reader.loading ? <LoadingState label="Đang mở tài liệu…" /> : null}
            {readerError ? <ErrorState error={readerError} onRetry={() => openDocument(reader)} /> : null}
            {!reader.loading && !readerError ? (
              <pre className="doc-content">{visibleContent || "Không có dòng khớp từ khóa."}</pre>
            ) : null}
          </article>
        </div>
      ) : null}
    </section>
  );
}
