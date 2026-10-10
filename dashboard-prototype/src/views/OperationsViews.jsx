import { useEffect, useRef, useState } from "react";
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
import { useDebouncedValue, useObservedAt, useResource } from "../hooks.js";
import { connectionPresentation, formatPercent } from "../statusPresentation.js";
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
      <span>{value == null ? "Chưa biết" : formatBytes(value)}</span>
      <small>{growth == null ? "Chưa đủ hai mẫu" : `${growth >= 0 ? "+" : ""}${formatBytes(growth)} từ lần đo trước`}</small>
    </div>
  );
}

export function StorageView({ refreshKey, onCreatedAction, onToast, onNavigate }) {
  const resource = useResource(api.storage, [], refreshKey);
  const backups = useResource(api.backups, [], refreshKey);
  const [backupError, setBackupError] = useState(null);
  const [restoreHelp, setRestoreHelp] = useState(false);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState("");
  const createBackup = async () => {
    setBusy("backup");
    setBackupError(null);
    try {
      await api.createBackup();
      onToast("Đã tạo bản sao lưu. Tải lại danh sách để xem metadata.");
      await backups.reload();
    } catch (error) {
      setBackupError(error);
    } finally {
      setBusy("");
    }
  };

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
      <section className="panel page-panel" aria-label="Sao lưu và khôi phục">
        <PanelHeader eyebrow="DỮ LIỆU CỤC BỘ" title="Sao lưu và khôi phục" />
        <p>Bản sao lưu portable không chứa credential hay phiên đăng nhập Telegram.</p>
        <div className="ops-panel-actions">
          <button className="button button--primary" disabled={Boolean(busy)} onClick={createBackup}>{busy === "backup" ? "Đang tạo bản sao lưu…" : "Tạo bản sao lưu"}</button>
          <button className="button button--outline" onClick={backups.reload}>Tải lại danh sách sao lưu</button>
          <button className="button button--outline" aria-expanded={restoreHelp} onClick={() => setRestoreHelp(value => !value)}>Hướng dẫn khôi phục</button>
        </div>
        {backupError ? <ErrorState error={backupError} /> : null}
        {backups.error ? <ErrorState error={backups.error} onRetry={backups.reload} /> : null}
        {backups.loading && !backups.data ? <LoadingState label="Đang đọc danh sách sao lưu…" /> : null}
        <p>Danh sách chỉ đọc manifest và metadata; chưa kiểm tra checksum nội dung hay xác minh khôi phục thử.</p>
        {(backups.data?.items || []).map(item => <div className="ops-setting-row" key={item.id}><div><b>{item.id}</b><span>{formatBytes(item.size_bytes)} · Schema: {item.manifest?.schema_revision || "Chưa biết"}</span></div><Badge tone="yellow">{item.validation_state === "manifest_only" ? "CHỈ MANIFEST" : "CHƯA XÁC MINH"}</Badge></div>)}
        {backups.data && !backups.data.items?.length ? <p>Chưa có bản sao lưu trong danh sách.</p> : null}
        {restoreHelp ? <div className="ops-local-warning ops-local-warning--yellow"><div><b>Khôi phục trong ứng dụng Windows</b><p>Mở Launcher → Dữ liệu và sao lưu → chọn tệp sao lưu → đọc preview ảnh hưởng. Chọn Hủy nếu chưa muốn thay dữ liệu. Ứng dụng sẽ kiểm tra và khóa thao tác ghi trước khi khôi phục; danh sách này không chứng minh tệp khôi phục an toàn.</p><button className="button button--outline" onClick={() => onNavigate?.("documentation")}>Đọc hướng dẫn sử dụng</button></div></div> : null}
      </section>
      {resource.error && resource.data ? <p role="alert">DỮ LIỆU CŨ · Dung lượng chưa tải lại được.</p> : null}
      <section className="ops-storage-hero">
        <div>
          <p className="eyebrow">LOCAL STORAGE · LIVE</p>
          <h2>{[current?.data_bytes, current?.vector_bytes, current?.media_bytes].every(Number.isFinite) ? formatBytes(current.data_bytes + current.vector_bytes + current.media_bytes) : "Chưa biết"}</h2>
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
          <span><i className="storage-dot storage-dot--teal" />Data {current?.data_bytes == null ? "Chưa biết" : formatBytes(current.data_bytes)}</span>
          <span><i className="storage-dot storage-dot--yellow" />Vector {current?.vector_bytes == null ? "Chưa biết" : formatBytes(current.vector_bytes)}</span>
          <span><i className="storage-dot storage-dot--magenta" />Media {current?.media_bytes == null ? "Chưa biết" : formatBytes(current.media_bytes)}</span>
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
  const now = useObservedAt();
  if (resource.loading && !resource.data) return <LoadingState label="Đang tải worker telemetry…" />;
  if (resource.error && !resource.data)
    return <ErrorState error={resource.error} onRetry={resource.reload} />;
  const data = resource.data || {};
  const metric = data.latest_metric;
  const children = metric?.details?.children || [];
  const observation = connectionPresentation({ state: "ready", checked_at: metric?.collected_at }, now, Boolean(resource.error));

  return (
    <section className="ops-stack">
      <section className="ops-status-strip">
        <div>
          <Pulse size={28} />
          <b>Lịch scheduler đã cấu hình · {observation.online ? "Có mẫu mới" : observation.label}</b>
          <span>Mẫu gần nhất {formatDate(metric?.collected_at)}</span>
        </div>
        <Badge tone="paper">{data.scheduler?.length ?? "Chưa biết"} LỊCH CẤU HÌNH</Badge>
      </section>
      {resource.error ? <ErrorState error={resource.error} onRetry={resource.reload} /> : null}

      <section className="ops-resource-strip">
        <div><Memory size={30} /><span>RAM</span><b>{metric?.rss_bytes == null ? "Chưa biết" : formatBytes(metric.rss_bytes)}</b><Badge tone="teal">PID {metric?.process_id || "—"}</Badge></div>
        <div><Pulse size={30} /><span>CPU</span><b>{formatPercent(metric?.cpu_percent)}</b><Badge tone={observation.tone}>{observation.online ? "MẪU MỚI" : observation.label}</Badge></div>
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
        <div><Clock size={30} /><span>Queue</span><b>{metric?.queued_jobs == null ? "Chưa biết" : formatNumber(metric.queued_jobs)}</b><Badge tone="paper">{metric?.running_jobs ?? "Chưa biết"} RUNNING</Badge></div>
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
                    <td><Badge tone="paper">{job.pending ? "PENDING" : "ĐÃ CẤU HÌNH"}</Badge></td>
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
                    <td>{child.rss_bytes == null ? "Chưa biết" : formatBytes(child.rss_bytes)}</td>
                    <td>{formatPercent(child.cpu_percent)}</td>
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
                      disabled={resource.loading || Boolean(resource.error)}
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
  const columns = ["occurred_at", "action", "target_type", "outcome"];
  const escape = (value) => {
    const text = String(value ?? "");
    return `"${(/^[\s\u0000-\u001f]*[=+@-]/.test(text) ? "'" + text : text).replaceAll('"', '""')}"`;
  };
  const csv = [
    ["Thời gian UTC", "Hành động", "Loại đối tượng", "Kết quả"].map(escape).join(","),
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
  const [exporting, setExporting] = useState(false);
  const [exportedAt, setExportedAt] = useState(null);
  const exportScope = useRef(0);
  useEffect(() => { exportScope.current += 1; return () => { exportScope.current += 1; }; }, [query, outcome, page]);
  const downloadSupport = async () => {
    const scope = exportScope.current;
    setExporting(true);
    try {
      const result = await api.audit({ query, outcome, page, pageSize: 100, supportExport: true });
      if (scope !== exportScope.current) return;
      exportAudit(result.items);
      setExportedAt(new Date().toISOString());
      onToast("Đã bắt đầu tải CSV hỗ trợ của trang và bộ lọc hiện tại; hãy kiểm tra tải xuống của trình duyệt.");
    } catch (error) {
      if (scope === exportScope.current) onToast(error.message, "error");
    } finally {
      setExporting(false);
    }
  };
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
            disabled={exporting}
            onClick={downloadSupport}
          >
            <DownloadSimple size={18} />Xuất CSV
          </button>
        }
      />
      <p>CSV hỗ trợ: chỉ thời gian UTC, loại hành động, loại đối tượng và kết quả; tối đa 100 sự kiện của trang {page}, bộ lọc hiện tại, được đọc mới khi bấm xuất. Không giới hạn theo ngày. Không phải log đầy đủ; không chứa ID hay nội dung tự do.</p>
      {exportedAt ? <p role="status">Lần bắt đầu tải gần nhất: {formatDate(exportedAt)}. Kiểm tra tệp trong tải xuống của trình duyệt.</p> : null}
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
