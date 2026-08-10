import { useState } from "react";
import {
  ArrowsClockwise,
  CaretLeft,
  CaretRight,
  Clock,
  Database,
  DownloadSimple,
  Eye,
  HardDrives,
  MagnifyingGlass,
  Memory,
  Pulse,
  Wrench,
} from "@phosphor-icons/react";

import { api } from "../api.js";
import {
  formatBytes,
  formatDate,
  formatNumber,
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
} from "../ui.jsx";

function StorageMetric({ icon: Icon, label, value, growth }) {
  return (
    <div>
      <Icon size={24} weight="bold" />
      <b>{label}</b>
      <span>{formatBytes(value)}</span>
      <small>{growth == null ? "Chưa đủ hai mẫu" : `${growth >= 0 ? "+" : ""}${formatBytes(growth)} từ lần đo trước`}</small>
    </div>
  );
}

export function StorageView({ refreshKey, onCreatedAction, onToast }) {
  const resource = useResource(api.storage, [], refreshKey);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState("");

  const runPreview = async () => {
    setBusy("preview");
    try {
      setPreview(await api.cleanupPreview());
      onToast("Dry run hoàn tất; chưa có dữ liệu nào bị xóa.");
    } catch (error) {
      onToast(error.message, "error");
    } finally {
      setBusy("");
    }
  };

  const createAction = async () => {
    setBusy("action");
    try {
      const action = await api.createCleanupAction();
      onToast("Đã tạo cleanup PendingAction.");
      onCreatedAction(action);
    } catch (error) {
      onToast(error.message, "error");
    } finally {
      setBusy("");
    }
  };

  if (resource.loading && !resource.data) return <LoadingState label="Đang tải dung lượng…" />;
  if (resource.error && !resource.data)
    return <ErrorState error={resource.error} onRetry={resource.reload} />;
  const current = resource.data?.current;
  const growth = resource.data?.growth;
  const diskCapacity = current?.details?.disk_capacity;
  const diskPercent = diskCapacity?.total_bytes
    ? Math.min(100, (diskCapacity.used_bytes / diskCapacity.total_bytes) * 100)
    : null;

  return (
    <section className="ops-stack">
      <section className="ops-storage-hero">
        <div>
          <p className="eyebrow">LOCAL STORAGE · LIVE</p>
          <h2>{formatBytes((current?.data_bytes || 0) + (current?.vector_bytes || 0) + (current?.media_bytes || 0))}</h2>
          <span>{resource.data?.data_root || "Local data directory"}</span>
        </div>
        <div
          className="storage-donut"
          style={{ "--value": `${diskPercent ?? 0}%` }}
        >
          <b>{diskPercent == null ? "N/A" : `${diskPercent.toFixed(1)}%`}</b>
          <span>{diskPercent == null ? "CHƯA CÓ CAPACITY" : "Ổ ĐĨA ĐÃ DÙNG"}</span>
        </div>
        <div className="storage-breakdown">
          <span><i className="storage-dot storage-dot--teal" />Data {formatBytes(current?.data_bytes)}</span>
          <span><i className="storage-dot storage-dot--yellow" />Vector {formatBytes(current?.vector_bytes)}</span>
          <span><i className="storage-dot storage-dot--magenta" />Media {formatBytes(current?.media_bytes)}</span>
        </div>
      </section>

      <section className="panel">
        <PanelHeader
          eyebrow="CAPACITY TELEMETRY"
          title="Dung lượng theo lớp"
          action={<Badge tone="teal">{formatDate(current?.collected_at)}</Badge>}
        />
        {current ? (
          <div className="storage-list">
            <StorageMetric
              icon={Database}
              label="Application data"
              value={current.data_bytes}
              growth={growth?.data_bytes}
            />
            <StorageMetric
              icon={HardDrives}
              label="Qdrant vectors"
              value={current.vector_bytes}
              growth={growth?.vector_bytes}
            />
            <StorageMetric
              icon={Memory}
              label="Telegram media"
              value={current.media_bytes}
              growth={growth?.media_bytes}
            />
          </div>
        ) : (
          <EmptyState
            title="Chưa có telemetry dung lượng"
            description="Scheduler sẽ ghi mẫu trong vòng một phút."
          />
        )}
      </section>

      <section className="ops-cleanup">
        <div>
          <Wrench size={34} weight="bold" />
          <div>
            <p className="eyebrow">STORAGE CLEANUP</p>
            <h2>Dry run → Impact → PendingAction → Owner duyệt.</h2>
          </div>
        </div>
        <button
          className="button button--primary"
          disabled={Boolean(busy)}
          onClick={runPreview}
        >
          {busy === "preview" ? (
            <ArrowsClockwise className="spin" size={18} />
          ) : (
            <MagnifyingGlass size={18} />
          )}
          {busy === "preview" ? "Đang dry run…" : "Chạy dry run"}
        </button>
      </section>

      {preview ? (
        <section className="panel cleanup-preview">
          <PanelHeader
            eyebrow="IMPACT PREVIEW · LIVE"
            title="Chi tiết ảnh hưởng"
            action={<Badge tone="yellow">CHƯA XÓA DỮ LIỆU</Badge>}
          />
          <div className="cleanup-impact-list">
            <div><b>Message hết retention/quota</b><span>{formatNumber(preview.expired_messages)}</span></div>
            <div><b>Vector hết retention/quota</b><span>{formatNumber(preview.expired_vectors)}</span></div>
            <div><b>Orphan vectors</b><span>{formatNumber(preview.orphan_vectors)}</span></div>
            <div><b>Media files</b><span>{formatNumber(preview.media_files)}</span><strong>{formatBytes(preview.media_bytes)}</strong></div>
          </div>
          <div className="ops-panel-actions">
            <button className="button button--outline" onClick={() => setPreview(null)}>
              Hủy preview
            </button>
            <button
              className="button button--primary"
              disabled={Boolean(busy)}
              onClick={createAction}
            >
              <Eye size={18} />
              Tạo PendingAction
            </button>
          </div>
        </section>
      ) : null}
    </section>
  );
}

export function WorkersView({ refreshKey }) {
  const resource = useResource(api.workers, [], refreshKey);
  if (resource.loading && !resource.data) return <LoadingState label="Đang tải worker telemetry…" />;
  if (resource.error && !resource.data)
    return <ErrorState error={resource.error} onRetry={resource.reload} />;
  const data = resource.data || {};
  const metric = data.latest_metric;
  const children = metric?.details?.children || [];

  return (
    <section className="ops-stack">
      <section className="ops-status-strip">
        <div>
          <Pulse size={28} />
          <b>Scheduler đang kết nối</b>
          <span>Mẫu gần nhất {formatDate(metric?.collected_at)}</span>
        </div>
        <Badge tone="success">{data.scheduler?.length || 0} SCHEDULED JOB</Badge>
      </section>

      <section className="ops-resource-strip">
        <div><Memory size={30} /><span>RAM</span><b>{formatBytes(metric?.rss_bytes)}</b><Badge tone="teal">PID {metric?.process_id || "—"}</Badge></div>
        <div><Pulse size={30} /><span>CPU</span><b>{Number(metric?.cpu_percent || 0).toFixed(1)}%</b><Badge tone="paper">PROCESS</Badge></div>
        <div>
          <HardDrives size={30} />
          <span>VRAM</span>
          <b>
            {metric?.vram_bytes == null
              ? "Không khả dụng"
              : formatBytes(metric.vram_bytes)}
          </b>
          <Badge tone="paper">
            {metric?.details?.vram_status === "unavailable" ? "KHÔNG CÓ NVIDIA-SMI" : "GPU"}
          </Badge>
        </div>
        <div><Clock size={30} /><span>Queue</span><b>{formatNumber(metric?.queued_jobs)}</b><Badge tone={metric?.queued_jobs ? "yellow" : "success"}>{metric?.running_jobs || 0} RUNNING</Badge></div>
      </section>

      <section className="panel">
        <PanelHeader
          eyebrow="SCHEDULER · LIVE"
          title="Lịch chạy"
          action={<Badge tone="teal">{data.scheduler?.length || 0} JOB</Badge>}
        />
        {data.scheduler?.length ? (
          <div className="table-scroll">
            <table className="worker-detail-table">
              <thead><tr><th>Job</th><th>Next run</th><th>Max instances</th><th>Trạng thái</th></tr></thead>
              <tbody>
                {data.scheduler.map((job) => (
                  <tr key={job.id}>
                    <td><b>{job.id}</b></td>
                    <td>{formatDate(job.next_run_time)}</td>
                    <td>{job.max_instances}</td>
                    <td><Badge tone={job.pending ? "yellow" : "success"}>{job.pending ? "PENDING" : "SCHEDULED"}</Badge></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="Không có scheduler job" />
        )}
      </section>

      <section className="panel">
        <PanelHeader eyebrow="PROCESS TREE · LIVE" title="Tiến trình con" />
        {children.length ? (
          <div className="table-scroll">
            <table className="ops-table">
              <thead><tr><th>PID</th><th>Tên</th><th>RAM</th><th>CPU</th><th>VRAM</th></tr></thead>
              <tbody>
                {children.map((child) => (
                  <tr key={`${child.pid}-${child.name}`}>
                    <td>{child.pid}</td>
                    <td><b>{child.name}</b></td>
                    <td>{formatBytes(child.rss_bytes)}</td>
                    <td>{Number(child.cpu_percent || 0).toFixed(1)}%</td>
                    <td>
                      {child.vram_bytes == null ? "Không có telemetry" : formatBytes(child.vram_bytes)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState
            title="Không có tiến trình con"
            description="Runtime hiện đang chạy trong một tiến trình chính."
          />
        )}
      </section>

      <section className="panel">
        <PanelHeader
          eyebrow="ACTIVE BACKGROUND JOBS"
          title="Queue hiện tại"
          action={<Badge tone="paper">{data.jobs?.length || 0} JOB</Badge>}
        />
        {data.jobs?.length ? (
          <div className="table-scroll">
            <table className="ops-table">
              <thead><tr><th>Job</th><th>Loại</th><th>Trạng thái</th><th>Attempt</th><th>Worker</th><th>Lỗi</th></tr></thead>
              <tbody>
                {data.jobs.map((job) => (
                  <tr key={job.id}>
                    <td>{job.id}</td>
                    <td>{humanize(job.job_type)}</td>
                    <td><Badge tone={statusTone(job.status)}>{humanize(job.status)}</Badge></td>
                    <td>{job.attempts}/{job.max_attempts}</td>
                    <td>{job.locked_by || "—"}</td>
                    <td className="ops-cell-wrap">{job.last_error || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <EmptyState title="Queue đang trống" description="Không có background job cần xử lý." />
        )}
      </section>
    </section>
  );
}

export function ActionsView({ refreshKey, onReview }) {
  const [state, setState] = useState("all");
  const resource = useResource(() => api.pendingActions(state, 500), [state], refreshKey);

  return (
    <section className="panel page-panel">
      <PanelHeader
        eyebrow="OWNER CONTROL · LIVE"
        title="Hành động và xác nhận"
        action={<Badge tone="yellow">{resource.data?.items?.length || 0} ACTION</Badge>}
      />
      <div className="filter-bar">
        <select
          className="ops-filter-select"
          value={state}
          onChange={(event) => setState(event.target.value)}
        >
          <option value="all">Tất cả</option>
          <option value="pending">Đang chờ</option>
          <option value="confirmed">Đã xác nhận</option>
          <option value="executed">Đã thực thi</option>
          <option value="failed">Lỗi</option>
          <option value="cancelled">Đã hủy</option>
        </select>
      </div>
      {resource.loading && !resource.data ? <LoadingState label="Đang tải hành động…" /> : null}
      {resource.error && !resource.data ? <ErrorState error={resource.error} onRetry={resource.reload} /> : null}
      {resource.data?.items?.length ? (
        <div className="table-scroll">
          <table className="ops-table">
            <thead><tr><th>Hành động</th><th>Preview</th><th>Phạm vi</th><th>Trạng thái</th><th>Hết hạn</th><th>Thao tác</th></tr></thead>
            <tbody>
              {resource.data.items.map((action) => (
                <tr key={action.action_id}>
                  <td><b>{humanize(action.action_type)}</b><small className="table-sub">{action.action_id}</small></td>
                  <td className="ops-cell-wrap">{action.preview || "—"}</td>
                  <td>{action.chat_id || "Hệ thống"}</td>
                  <td><Badge tone={statusTone(action.status)}>{humanize(action.status)}</Badge></td>
                  <td>{formatDate(action.expires_at)}</td>
                  <td>
                    <button
                      className="button button--small button--outline"
                      onClick={() => onReview(action)}
                    >
                      <Eye size={16} />Xem
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : resource.data ? (
        <EmptyState title="Không có hành động" description="Không có bản ghi ở trạng thái đã chọn." />
      ) : null}
    </section>
  );
}

function exportAudit(items) {
  const columns = ["occurred_at", "action", "target_type", "target_id", "outcome", "reason", "correlation_id"];
  const escape = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
  const csv = [
    columns.join(","),
    ...items.map((item) => columns.map((column) => escape(item[column])).join(",")),
  ].join("\r\n");
  const blob = new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `telegram-ai-audit-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

export function AuditView({ refreshKey, onToast }) {
  const [query, setQuery] = useState("");
  const [outcome, setOutcome] = useState("");
  const [page, setPage] = useState(1);
  const debounced = useDebouncedValue(query);
  const resource = useResource(
    () => api.audit({ query: debounced, outcome, page, pageSize: 100 }),
    [debounced, outcome, page],
    refreshKey,
  );

  return (
    <section className="panel page-panel">
      <PanelHeader
        eyebrow="AUDIT TRAIL · LIVE"
        title="Audit Log"
        action={
          <button
            className="button button--primary"
            disabled={!resource.data?.items?.length}
            onClick={() => {
              exportAudit(resource.data.items);
              onToast("Đã xuất audit CSV từ dữ liệu đang hiển thị.");
            }}
          >
            <DownloadSimple size={18} />Xuất CSV
          </button>
        }
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
            placeholder="Tìm action, target hoặc lý do…"
          />
        </label>
        <select
          className="ops-filter-select"
          value={outcome}
          onChange={(event) => {
            setOutcome(event.target.value);
            setPage(1);
          }}
        >
          <option value="">Tất cả kết quả</option>
          <option value="success">Success</option>
          <option value="pending">Pending</option>
          <option value="confirmed">Confirmed</option>
          <option value="failed">Failed</option>
          <option value="blocked">Blocked</option>
        </select>
      </div>
      {resource.loading && !resource.data ? <LoadingState label="Đang tải audit…" /> : null}
      {resource.error && !resource.data ? <ErrorState error={resource.error} onRetry={resource.reload} /> : null}
      {resource.data?.items?.length ? (
        <div className="table-scroll">
          <table className="ops-table">
            <thead><tr><th>Thời gian</th><th>Action</th><th>Target</th><th>Kết quả</th><th>Lý do</th><th>Correlation</th></tr></thead>
            <tbody>
              {resource.data.items.map((event) => (
                <tr key={event.id}>
                  <td>{formatDate(event.occurred_at)}</td>
                  <td><b>{humanize(event.action)}</b></td>
                  <td>{event.target_id || event.target_type || "—"}</td>
                  <td><Badge tone={statusTone(event.outcome)}>{humanize(event.outcome)}</Badge></td>
                  <td className="ops-cell-wrap">{event.reason || "—"}</td>
                  <td>{event.correlation_id || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : resource.data ? (
        <EmptyState title="Không có audit phù hợp" />
      ) : null}
      {resource.data ? (
        <div className="table-footer">
          <span>Trang {page} · {formatNumber(resource.data.total)} sự kiện</span>
          <div className="pagination">
            <button disabled={page <= 1} onClick={() => setPage((value) => value - 1)}><CaretLeft size={18} />Trước</button>
            <button disabled={page * 100 >= resource.data.total} onClick={() => setPage((value) => value + 1)}>Sau<CaretRight size={18} /></button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
