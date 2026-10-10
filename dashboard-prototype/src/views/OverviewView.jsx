import {
  Clock,
  Cpu,
  Database,
  HardDrives,
  PaperPlaneTilt,
  Pulse,
  ShieldCheck,
  Sparkle,
  TerminalWindow,
} from "@phosphor-icons/react";

import { api } from "../api.js";
import { formatBytes, formatDate, formatNumber, humanize, statusTone } from "../format.js";
import { useObservedAt, useResource } from "../hooks.js";
import { connectionPresentation } from "../statusPresentation.js";
import {
  Badge,
  EmptyState,
  ErrorState,
  LoadingState,
  PanelHeader,
} from "../ui.jsx";

function StatBlock({ label, value, note, tone, icon: Icon }) {
  return (
    <article className={`stat-block stat-block--${tone}`}>
      <div className="stat-topline">
        <span>{label}</span>
        <small>24 GIỜ</small>
        <Icon size={24} weight="bold" aria-hidden="true" />
      </div>
      <strong>{value}</strong>
      <div className="stat-foot">
        <span>{note}</span>
      </div>
    </article>
  );
}

function RuntimeChart({ history = [] }) {
  const points = [...history].reverse().filter(Boolean);
  const sampled =
    points.length <= 8
      ? points
      : points.filter((_, index) => index % Math.ceil(points.length / 8) === 0).slice(-8);
  const maxValue = Math.max(
    1,
    ...sampled.flatMap((item) => [
      item.rss_bytes || 0,
      item.vector_bytes || 0,
      item.data_bytes || 0,
    ]),
  );
  const height = (value) => `${Math.max(0, Math.round(((value || 0) / maxValue) * 100))}%`;

  return (
    <section className="panel activity-panel">
      <PanelHeader
        eyebrow="RUNTIME TELEMETRY"
        title="Tài nguyên theo thời gian"
        action={<Badge tone="teal">{sampled.length} MẪU</Badge>}
      />
      {sampled.length ? (
        <>
          <div className="legend" aria-label="Chú thích biểu đồ">
            <span><i className="legend-dot legend-dot--teal" />RAM tiến trình</span>
            <span><i className="legend-dot legend-dot--yellow" />Vector</span>
            <span><i className="legend-dot legend-dot--magenta" />Dữ liệu</span>
          </div>
          <div className="chart-wrap">
            <div className="bar-chart" role="img" aria-label="Biểu đồ tài nguyên runtime">
              {sampled.map((point) => (
                <div className="bar-column" key={point.id}>
                  <div className="bars">
                    <span className="bar bar--local" style={{ height: height(point.rss_bytes) }} />
                    <span className="bar bar--cloud" style={{ height: height(point.vector_bytes) }} />
                    <span className="bar bar--blocked" style={{ height: height(point.data_bytes) }} />
                  </div>
                  <small>
                    {new Date(point.collected_at).toLocaleTimeString("vi-VN", {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </small>
                </div>
              ))}
            </div>
          </div>
          <div className="chart-summary">
            <b>{sampled.at(-1)?.rss_bytes == null ? "—" : formatBytes(sampled.at(-1).rss_bytes)}</b>
            <span>RAM của tiến trình tại lần đo gần nhất.</span>
          </div>
        </>
      ) : (
        <EmptyState
          title="Chưa có mẫu telemetry"
          description="Mở telemetry hoặc kiểm tra ứng dụng Windows để xem lần đo tiếp theo."
        />
      )}
    </section>
  );
}

function HealthPanel({ overview, onNavigate, observedAt, unavailable }) {
  const health = overview.health || [];
  const ready = health.length > 0 && health.every(item => connectionPresentation(item, observedAt, unavailable).online);
  return (
    <section className="panel pulse-panel">
      <PanelHeader
        eyebrow="SERVICE HEALTH"
        title="Nhịp hệ thống"
        action={
          <Badge tone={ready ? "success" : "yellow"}>
            {health.length ? `${health.length} SERVICE` : "CHƯA BIẾT"}
          </Badge>
        }
      />
      <div className="service-list">
        {health.length ? (
          health.map((service) => {
            const presentation = connectionPresentation(service, observedAt, unavailable);
            return (
            <div className="service-row" key={service.component}>
              <span
                className={`service-light service-light--${
                  presentation.online ? "online" : "warning"
                }`}
              />
              <div>
                <b>{humanize(service.component)}</b>
                <small>Cập nhật {formatDate(service.checked_at)}</small>
                {!presentation.online ? <small>{service.next_action || "Mở Kết nối và bấm Kiểm tra lại."}</small> : null}
              </div>
              <strong>
                {presentation.label}{presentation.online && service.latency_ms != null ? ` · ${service.latency_ms} ms` : ""}
              </strong>
            </div>
          ); })
        ) : (
          <div className="service-row">
            <span className="service-light service-light--warning" />
            <div>
              <b>Admin API</b>
              <small>Mở Kết nối và bấm Kiểm tra lại.</small>
            </div>
            <strong>CHƯA BIẾT</strong>
          </div>
        )}
      </div>
      <div className="pulse-actions">
        <button className="text-action" onClick={() => onNavigate("connections")}>Kiểm tra kết nối</button>
        <button className="text-action" onClick={() => onNavigate("workers")}>
          Mở telemetry
          <Pulse size={18} weight="bold" />
        </button>
        <button className="text-action" onClick={() => onNavigate("storage")}>
          Xem dung lượng
          <HardDrives size={18} weight="bold" />
        </button>
      </div>
    </section>
  );
}

function PendingTable({ actions, onReview, onNavigate }) {
  return (
    <section className="panel pending-panel">
      <PanelHeader
        eyebrow="OWNER CONFIRMATION · LIVE"
        title="Hành động chờ duyệt"
        action={<Badge tone="yellow">{actions.length} CHỜ</Badge>}
      />
      {actions.length ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Hành động</th>
                <th>Phạm vi</th>
                <th>Hết hạn</th>
                <th><span className="sr-only">Thao tác</span></th>
              </tr>
            </thead>
            <tbody>
              {actions.slice(0, 5).map((action) => (
                <tr key={action.action_id}>
                  <td>
                    <div className="action-title">
                      <span className="action-mark action-mark--yellow" />
                      <div>
                        <b>{humanize(action.action_type)}</b>
                        <small>{action.action_id}</small>
                      </div>
                    </div>
                  </td>
                  <td>{action.chat_id || "Hệ thống"}</td>
                  <td><Badge tone="paper" icon={Clock}>{formatDate(action.expires_at)}</Badge></td>
                  <td>
                    <button
                      className="button button--small button--outline"
                      onClick={() => onReview(action)}
                    >
                      Xem & duyệt
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState
          title="Không có hành động chờ"
          description="Mọi yêu cầu nhạy cảm đã được owner xử lý."
        />
      )}
      {actions.length > 5 ? (
        <button className="text-action" onClick={() => onNavigate("actions")}>
          Xem tất cả
          <Clock size={18} weight="bold" />
        </button>
      ) : null}
    </section>
  );
}

function AuditFeed({ items, onNavigate }) {
  return (
    <section className="panel event-panel">
      <PanelHeader
        eyebrow="AUDIT TRAIL · LIVE"
        title="Sự kiện mới nhất"
        action={<TerminalWindow size={25} weight="bold" />}
      />
      {items.length ? (
        <div className="event-list">
          {items.slice(0, 6).map((event) => (
            <div className="event-row" key={event.id}>
              <time>
                {new Date(event.occurred_at).toLocaleTimeString("vi-VN", {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </time>
              <span className={`event-line event-line--${statusTone(event.outcome)}`} />
              <div>
                <b>{humanize(event.action)}</b>
                <small>{event.target_id || event.target_type || "Runtime"}</small>
              </div>
              <Badge tone={statusTone(event.outcome)}>{humanize(event.outcome)}</Badge>
            </div>
          ))}
        </div>
      ) : (
        <EmptyState title="Chưa có audit" description="Các sự kiện runtime sẽ xuất hiện tại đây." />
      )}
      <button className="text-action" onClick={() => onNavigate("audit")}>
        Mở audit log
        <TerminalWindow size={18} weight="bold" />
      </button>
    </section>
  );
}

export function OverviewView({ refreshKey, onReview, onNavigate }) {
  const overview = useResource(api.overview, [], refreshKey);
  const workers = useResource(api.workers, [], refreshKey);
  const pending = useResource(() => api.pendingActions("pending", 20), [], refreshKey);
  const audit = useResource(() => api.audit({ pageSize: 10 }), [], refreshKey);
  const observedAt = useObservedAt();

  if (overview.loading && !overview.data) return <LoadingState label="Đang tải tổng quan…" />;
  if (overview.error && !overview.data)
    return <ErrorState error={overview.error} onRetry={overview.reload} />;

  const data = overview.data || {};
  const runtime = data.runtime;
  const health = data.health || [];
  const ready = health.length > 0 && health.every(item => connectionPresentation(item, observedAt, Boolean(overview.error)).online);
  return (
    <>
      {overview.error ? <section className="panel"><p>DỮ LIỆU CŨ · Tổng quan chưa cập nhật được. Kiểm tra lại để đọc trạng thái hiện tại.</p><ErrorState error={overview.error} onRetry={overview.reload} /></section> : null}
      {[[workers, "Telemetry"], [pending, "Hành động chờ"], [audit, "Audit"]].map(([resource, label]) => resource.error && resource.data ? <section className="panel" key={label}><p>DỮ LIỆU CŨ · {label}</p><ErrorState error={resource.error} onRetry={resource.reload} /></section> : null)}
      <section className="overview-context-strip" aria-label="Trạng thái hệ thống">
        <div>
          <span>Trạng thái đã đo</span>
          <b>{ready ? "SẴN SÀNG" : overview.error ? "DỮ LIỆU CŨ" : "CHƯA XÁC NHẬN"}</b>
          <small>{ready ? "Kết quả kiểm tra trong một phút" : "Mở Kết nối và bấm Kiểm tra lại"}</small>
        </div>
        <div>
          <span>Global AI provider</span>
          <b>{data.ai_provider == null ? "CHƯA BIẾT" : String(data.ai_provider).toUpperCase()}</b>
          <small>{data.ai_model || (data.ai_provider === "off" ? "AI đang tắt" : "Chưa có thông tin model")}</small>
        </div>
        <button className="button button--primary" onClick={() => onNavigate("telegram-features")}>
          <PaperPlaneTilt size={19} weight="fill" />
          Chức năng Telegram
        </button>
      </section>

      <section className="stats-grid" aria-label="Chỉ số 24 giờ">
        <StatBlock
          label="Tin nhắn 24 giờ"
          value={data.messages == null ? "—" : formatNumber(data.messages)}
          note={data.joined_sources == null ? "Chưa có số nguồn đã tham gia" : `Trên ${formatNumber(data.joined_sources)} nguồn đã tham gia`}
          tone="teal"
          icon={PaperPlaneTilt}
        />
        <StatBlock
          label="Yêu cầu /ask"
          value={data.ask_requests == null ? "—" : formatNumber(data.ask_requests)}
          note="Câu hỏi bot ghi nhận · không gồm embedding"
          tone="paper"
          icon={Sparkle}
        />
        <StatBlock
          label="Hành động chờ"
          value={data.pending_actions == null ? "—" : String(data.pending_actions).padStart(2, "0")}
          note="Cần owner xác nhận"
          tone="yellow"
          icon={Clock}
        />
        <StatBlock
          label="Bị chặn hoặc lỗi"
          value={data.blocked_or_failed == null ? "—" : formatNumber(data.blocked_or_failed)}
          note="Audit trong 24 giờ"
          tone="magenta"
          icon={ShieldCheck}
        />
      </section>

      <section className="ops-resource-strip live-resource-strip">
        <div>
          <Cpu size={30} />
          <span>RAM</span>
          <b>{runtime?.rss_bytes == null ? "—" : formatBytes(runtime.rss_bytes)}</b>
          <Badge tone="teal">PID {runtime?.pid ?? "—"}</Badge>
        </div>
        <div>
          <Database size={30} />
          <span>Dữ liệu</span>
          <b>{runtime?.data_bytes == null ? "—" : formatBytes(runtime.data_bytes)}</b>
          <Badge tone="paper">DỮ LIỆU</Badge>
        </div>
        <div>
          <HardDrives size={30} />
          <span>Vector</span>
          <b>{runtime?.vector_bytes == null ? "—" : formatBytes(runtime.vector_bytes)}</b>
          <Badge tone="yellow">{runtime?.queued_jobs ?? "—"} QUEUED</Badge>
        </div>
        <div>
          <Cpu size={30} />
          <span>VRAM</span>
          <b>
            {runtime?.vram_bytes == null
              ? "Không khả dụng"
              : formatBytes(runtime.vram_bytes)}
          </b>
          <Badge tone="paper">GPU PROCESS</Badge>
        </div>
      </section>

      <section className="dashboard-grid dashboard-grid--top">
        {workers.error && !workers.data ? (
          <section className="panel"><ErrorState error={workers.error} onRetry={workers.reload} /></section>
        ) : (
          <RuntimeChart history={workers.data?.history || []} />
        )}
        <HealthPanel overview={data} onNavigate={onNavigate} observedAt={observedAt} unavailable={Boolean(overview.error)} />
      </section>
      <section className="dashboard-grid dashboard-grid--bottom">
        {pending.error && !pending.data ? <section className="panel"><ErrorState error={pending.error} onRetry={pending.reload} /></section> : pending.loading && !pending.data ? <LoadingState label="Đang tải hành động chờ…" /> : <PendingTable
          actions={pending.data?.items || []}
          onReview={onReview}
          onNavigate={onNavigate}
        />}
        {audit.error && !audit.data ? <section className="panel"><ErrorState error={audit.error} onRetry={audit.reload} /></section> : audit.loading && !audit.data ? <LoadingState label="Đang tải audit…" /> : <AuditFeed items={audit.data?.items || []} onNavigate={onNavigate} />}
      </section>
    </>
  );
}
