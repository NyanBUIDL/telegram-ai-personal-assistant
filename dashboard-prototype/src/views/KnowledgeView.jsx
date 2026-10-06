import { useEffect, useMemo, useState } from "react";
import {
  ArrowsClockwise,
  Brain,
  CaretDown,
  CaretLeft,
  CaretRight,
  CaretUp,
  DownloadSimple,
  MagnifyingGlass,
  NotePencil,
  Pause,
  Play,
  Trash,
  Warning,
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

function downloadSources(blob) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `telegram-learning-sources-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

function normalizeJob(job) {
  const status = String(job.status || "unknown").toLowerCase();
  const phase = String(job.phase || "unknown").toLowerCase();
  const rawProgress =
    typeof job.progress === "number" && Number.isFinite(job.progress)
      ? job.progress
      : null;
  let progress = rawProgress;
  // The API owns completion progress. Do not turn an incomplete historical
  // payload green merely because its status string says completed.
  if (status === "completed" && progress == null) progress = null;
  else if (status === "queued") progress = 0;
  else if (status === "running" && phase === "syncing" && progress != null) {
    progress = Math.max(1, Math.min(50, progress));
  } else if (status === "running" && phase === "embedding" && progress != null) {
    progress = Math.max(51, Math.min(99, progress));
  } else if (progress != null) {
    progress = Math.max(0, Math.min(100, progress));
  }
  return { ...job, status, phase, progress };
}

function formatDuration(value) {
  const milliseconds = Number(value);
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "—";
  const seconds = Math.round(milliseconds / 1000);
  if (seconds < 60) return `${seconds} giây`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} phút ${seconds % 60} giây`;
}

const JOB_LABELS = {
  queued: "Đang chờ",
  running: "Đang chạy",
  syncing: "Đồng bộ Telegram",
  embedding: "Tạo embedding",
  completed: "Hoàn tất",
  completed_with_warning: "Hoàn tất có cảnh báo",
  reconciliation_required: "Cần đối chiếu",
  failed: "Lỗi",
  paused: "Tạm dừng",
  pausing: "Đang tạm dừng",
  unknown: "Không xác định",
};

function jobLabel(value) {
  return JOB_LABELS[value] || humanize(value);
}

export function KnowledgeView({ refreshKey, onCreatedAction, onToast }) {
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [chatType, setChatType] = useState("");
  const [autoKnowledge, setAutoKnowledge] = useState("");
  const [sortBy, setSortBy] = useState("title");
  const [sortDir, setSortDir] = useState("asc");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [busy, setBusy] = useState("");
  const [selected, setSelected] = useState(() => new Set());
  const [viewMode, setViewMode] = useState(() =>
    window.matchMedia("(max-width: 720px)").matches ? "card" : "table",
  );
  const [expandedSources, setExpandedSources] = useState(() => new Set());
  useEffect(() => {
    const media = window.matchMedia("(max-width: 720px)");
    const syncMode = () => setViewMode(media.matches ? "card" : "table");
    media.addEventListener("change", syncMode);
    return () => media.removeEventListener("change", syncMode);
  }, []);
  const debouncedQuery = useDebouncedValue(query);
  const sources = useResource(
    () =>
      api.knowledgeSources({
        status,
        query: debouncedQuery,
        chatType,
        autoKnowledge,
        sortBy,
        sortDir,
        page,
        pageSize,
      }),
    [status, debouncedQuery, chatType, autoKnowledge, sortBy, sortDir, page, pageSize],
    refreshKey,
  );
  const jobs = useResource(() => api.learningJobs({ limit: 20 }), [], refreshKey);
  const normalizedJobs = useMemo(
    () => (jobs.data?.items || []).map(normalizeJob),
    [jobs.data],
  );

  const jobCounts = useMemo(() => {
    const counts = {};
    for (const job of normalizedJobs) {
      counts[job.status] = (counts[job.status] || 0) + 1;
    }
    return counts;
  }, [normalizedJobs]);

  const run = async (key, operation, success) => {
    setBusy(key);
    try {
      await operation();
      onToast(success);
      await Promise.all([sources.reload(), jobs.reload()]);
    } catch (error) {
      onToast(error.message, "error");
    } finally {
      setBusy("");
    }
  };

  const changeJob = (job, operation) =>
    run(
      `${job.id}-${operation}`,
      () => api.changeLearningJob(job.id, operation),
      `Job ${job.id} đã được chuyển sang ${operation}.`,
    );

  const exportAllSources = async () => {
    setBusy("export");
    try {
      downloadSources(await api.exportKnowledgeSources());
      onToast("Đã xuất đầy đủ danh sách nguồn học, không chỉ trang đang xem.");
    } catch (error) {
      onToast(error.message, "error");
    } finally {
      setBusy("");
    }
  };

  const createEnableAllAction = async () => {
    setBusy("enable-all");
    try {
      const action = await api.createEnableAllKnowledgeAction();
      onCreatedAction(action);
      onToast("Đã tạo bản xem trước; hãy xác nhận để đưa các nguồn còn thiếu vào hàng đợi.");
    } catch (error) {
      onToast(error.message, "error");
    } finally {
      setBusy("");
    }
  };

  const createSelectionAction = async () => {
    setBusy("selection");
    try {
      const action = await api.createEnableSelectionAction([...selected]);
      onCreatedAction(action);
      onToast(`Đã tạo preview học ${selected.size} nguồn được chọn.`);
    } catch (error) {
      onToast(error.message, "error");
    } finally {
      setBusy("");
    }
  };

  const selectAllFiltered = async () => {
    setBusy("select-filtered");
    try {
      const result = await api.knowledgeSources({
        status,
        query: debouncedQuery,
        chatType,
        autoKnowledge,
        sortBy,
        sortDir,
        page: 1,
        pageSize: 500,
      });
      setSelected(new Set(result.items.map((item) => item.chat_id)));
      onToast(`Đã chọn ${result.items.length}/${result.total} nguồn theo bộ lọc.`);
    } catch (error) {
      onToast(error.message, "error");
    } finally {
      setBusy("");
    }
  };

  const editNote = async (source) => {
    const note = window.prompt(
      `Ghi chú owner cho ${source.title || source.chat_id}:`,
      source.owner_note || "",
    );
    if (note === null) return;
    await run(
      `note-${source.chat_id}`,
      () => api.setKnowledgeNote(source.chat_id, note || null),
      "Đã lưu ghi chú cho nguồn.",
    );
  };

  const previewVectorDelete = async (source) => {
    setBusy(`delete-${source.chat_id}`);
    try {
      const action = await api.previewDeleteKnowledge(source.chat_id, "vectors_only");
      onCreatedAction(action);
      onToast("Đã tạo preview xóa vector; chưa xóa dữ liệu.");
    } catch (error) {
      onToast(error.message, "error");
    } finally {
      setBusy("");
    }
  };

  const toggleSourceSelection = (chatId, checked) => {
    const next = new Set(selected);
    if (checked) next.add(chatId);
    else next.delete(chatId);
    setSelected(next);
  };

  const toggleSourceExpanded = (chatId) => {
    const next = new Set(expandedSources);
    if (next.has(chatId)) next.delete(chatId);
    else next.add(chatId);
    setExpandedSources(next);
  };

  const summary = sources.data?.summary || {};
  const statusCounts = summary.counts || {};

  return (
    <section className="ops-stack">
      <section className="ops-resource-strip knowledge-kpis">
        <div>
          <Brain size={30} />
          <span>Tổng nguồn</span>
          <b>{formatNumber(summary.total_sources)}</b>
          <Badge tone="teal">MYSQL</Badge>
        </div>
        <div>
          <Play size={30} />
          <span>Đã học</span>
          <b>{formatNumber(summary.learned_sources)}</b>
          <Badge tone="success">{summary.coverage_percent || 0}% ĐỘ PHỦ</Badge>
        </div>
        <div>
          <Pause size={30} />
          <span>Đang chờ / tạm dừng</span>
          <b>{formatNumber((jobCounts.queued || 0) + (jobCounts.paused || 0))}</b>
          <Badge tone="yellow">QUEUE</Badge>
        </div>
        <div>
          <Warning size={30} />
          <span>Chưa học / không có nội dung</span>
          <b>
            {formatNumber(
              (statusCounts.not_learned || 0) + (statusCounts.no_content || 0),
            )}
          </b>
          <Badge
            tone={
              (statusCounts.not_learned || 0) + (statusCounts.no_content || 0)
                ? "yellow"
                : "success"
            }
          >
            {jobCounts.failed ? `${jobCounts.failed} JOB LỖI` : "ĐÃ RÀ SOÁT"}
          </Badge>
        </div>
      </section>

      <section className="integration-readiness integration-readiness--live">
        <div>
          <p className="eyebrow">UNIFIED TELEGRAM BRAIN · LIVE</p>
          <h2>
            {formatNumber(summary.learned_sources)}/{formatNumber(summary.total_sources)} nguồn
            đang đóng góp vào bộ não chung.
          </h2>
          <span>
            “Học” ở đây là đồng bộ nội dung được cấp quyền vào SQLite, làm sạch và tạo
            embedding để tìm kiếm ngữ nghĩa. Hệ thống không tự fine-tune model AI.
          </span>
          <small>
            Quyền tự học: {formatNumber(summary.auto_knowledge_sources)} nguồn · SQLite:{" "}
            {formatNumber(summary.mysql_messages)} tin · Vector: {formatNumber(summary.vectors)} ·
            lần học gần nhất: {formatDate(summary.last_learned_at)}
          </small>
        </div>
        <button
          className="button button--primary"
          disabled={
            Boolean(busy) ||
            !summary.total_sources ||
            (summary.auto_knowledge_sources >= summary.total_sources &&
              !statusCounts.not_learned &&
              !statusCounts.no_content &&
              !statusCounts.failed)
          }
          onClick={createEnableAllAction}
        >
          <Brain size={18} />
          {busy === "enable-all" ? "Đang tạo preview…" : "Đưa nguồn còn thiếu vào bộ não"}
        </button>
      </section>

      <section className="panel page-panel">
        <PanelHeader
          eyebrow="LEARNING INVENTORY · LIVE"
          title="Kho tri thức"
          action={
            <div className="panel-heading-actions">
              <div className="segment-control knowledge-view-toggle" role="group" aria-label="Kiểu hiển thị nguồn">
                <button
                  className={viewMode === "table" ? "is-active" : ""}
                  onClick={() => setViewMode("table")}
                >
                  Bảng
                </button>
                <button
                  className={viewMode === "card" ? "is-active" : ""}
                  onClick={() => setViewMode("card")}
                >
                  Card
                </button>
              </div>
              <button
                className="button button--primary"
                disabled={!summary.total_sources || Boolean(busy)}
                onClick={exportAllSources}
              >
                <DownloadSimple size={18} />
                {busy === "export" ? "Đang xuất…" : "Xuất toàn bộ CSV"}
              </button>
            </div>
          }
        />
          <div className="filter-bar knowledge-filter-bar">
          <label className="search-field">
            <MagnifyingGlass size={20} />
            <input
              value={query}
              onChange={(event) => { setQuery(event.target.value); setPage(1); }}
              placeholder="Tìm tên, @username, Chat ID hoặc ghi chú…"
            />
          </label>
          <select
            className="ops-filter-select"
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setPage(1);
            }}
          >
            <option value="">Tất cả trạng thái</option>
            <option value="learned">Đã học</option>
            <option value="learned_warning">Đã học, lần cập nhật gần nhất lỗi</option>
            <option value="queued">Đang chờ</option>
            <option value="running">Đang học</option>
            <option value="failed">Lỗi</option>
            <option value="not_learned">Chưa học</option>
            <option value="no_content">Không lấy được nội dung</option>
          </select>
          <select
            className="ops-filter-select"
            value={chatType}
            onChange={(event) => { setChatType(event.target.value); setPage(1); }}
            aria-label="Loại nguồn"
          >
            <option value="">Mọi loại nguồn</option>
            <option value="group">Group</option>
            <option value="supergroup">Supergroup</option>
            <option value="channel">Channel</option>
          </select>
          <select
            className="ops-filter-select"
            value={autoKnowledge}
            onChange={(event) => { setAutoKnowledge(event.target.value); setPage(1); }}
            aria-label="Quyền tự học"
          >
            <option value="">AUTO: tất cả</option>
            <option value="true">AUTO đang bật</option>
            <option value="false">AUTO đang tắt</option>
          </select>
          <select
            className="ops-filter-select"
            value={sortBy}
            onChange={(event) => { setSortBy(event.target.value); setPage(1); }}
            aria-label="Sắp xếp nguồn"
          >
            <option value="title">Sắp xếp theo tên</option>
            <option value="status">Theo trạng thái</option>
            <option value="messages">Theo số tin</option>
            <option value="vectors">Theo vector</option>
            <option value="storage">Theo dung lượng</option>
            <option value="last_learned">Theo lần học</option>
          </select>
          <select
            className="ops-filter-select"
            value={sortDir}
            onChange={(event) => { setSortDir(event.target.value); setPage(1); }}
            aria-label="Chiều sắp xếp"
          >
            <option value="asc">Tăng dần</option>
            <option value="desc">Giảm dần</option>
          </select>
          <div className="ops-panel-actions">
            <button
              className="button button--outline"
              disabled={Boolean(busy)}
              onClick={() =>
                run(
                  "pause-all",
                  api.pauseAllLearning,
                  "Đã gửi yêu cầu tạm dừng toàn bộ learning job.",
                )
              }
            >
              <Pause size={18} />
              Pause tất cả
            </button>
            <button
              className="button button--outline"
              disabled={Boolean(busy)}
              onClick={() =>
                run(
                  "resume-all",
                  api.resumeAllLearning,
                  "Đã tiếp tục toàn bộ learning job đang tạm dừng.",
                )
              }
            >
              <Play size={18} />
              Resume tất cả
            </button>
          </div>
        </div>
        {selected.size ? (
          <div className="selection-toolbar knowledge-bulk-toolbar">
            <b>{selected.size} nguồn đã chọn trên danh sách hiện tại</b>
            <button className="button button--outline" disabled={Boolean(busy)} onClick={selectAllFiltered}>
              Chọn toàn bộ kết quả lọc
            </button>
            <button className="button button--outline" disabled={Boolean(busy)} onClick={() => setSelected(new Set())}>
              Bỏ chọn
            </button>
            <button className="button button--primary" disabled={Boolean(busy)} onClick={createSelectionAction}>
              <Brain size={18} />
              Học nguồn đã chọn
            </button>
          </div>
        ) : null}

        {sources.loading && !sources.data ? <LoadingState label="Đang tải kho tri thức…" /> : null}
        {sources.error && !sources.data ? (
          <ErrorState error={sources.error} onRetry={sources.reload} />
        ) : null}
        {sources.data?.items?.length && viewMode === "table" ? (
          <div className="table-scroll knowledge-table-wrap">
            <table className="ops-table knowledge-source-table">
              <thead>
                <tr>
                  <th className="checkbox-cell">
                    <input
                      type="checkbox"
                      aria-label="Chọn tất cả nguồn trên trang"
                      checked={
                        Boolean(sources.data.items.length) &&
                        sources.data.items.every((item) => selected.has(item.chat_id))
                      }
                      onChange={(event) => {
                        const next = new Set(selected);
                        for (const item of sources.data.items) {
                          if (event.target.checked) next.add(item.chat_id);
                          else next.delete(item.chat_id);
                        }
                        setSelected(next);
                      }}
                    />
                  </th>
                  <th>Nguồn</th>
                  <th>Trạng thái</th>
                  <th>SQLite</th>
                  <th>Vector</th>
                  <th>Dung lượng</th>
                  <th>Lần học</th>
                  <th>Lỗi / ghi chú</th>
                  <th>Thao tác</th>
                </tr>
              </thead>
              <tbody>
                {sources.data.items.map((source) => (
                  <tr key={source.chat_id}>
                    <td className="checkbox-cell">
                      <input
                        type="checkbox"
                        aria-label={`Chọn ${source.title || source.chat_id}`}
                        checked={selected.has(source.chat_id)}
                        onChange={(event) =>
                          toggleSourceSelection(source.chat_id, event.target.checked)
                        }
                      />
                    </td>
                    <td>
                      <b>{source.title || "Chưa có tên"}</b>
                      <small className="table-sub">
                        {source.chat_id} · {source.chat_type}
                      </small>
                    </td>
                    <td>
                      <Badge tone={statusTone(source.status)}>{humanize(source.status)}</Badge>
                      <small className="table-sub">{source.auto_knowledge ? "AUTO ON" : "AUTO OFF"}</small>
                    </td>
                    <td>
                      <b>{formatNumber(source.mysql_message_count)}</b>
                      <small className="table-sub">
                        {formatNumber(source.text_message_count)} có nội dung
                      </small>
                    </td>
                    <td>{formatNumber(source.vector_count)}</td>
                    <td>
                      {formatBytes(
                        (source.message_storage_bytes || 0) +
                          (source.media_storage_bytes || 0),
                      )}
                    </td>
                    <td>{formatDate(source.last_learned_at)}</td>
                    <td className="ops-cell-wrap">
                      {source.last_error || source.owner_note || source.status_reason || "—"}
                    </td>
                    <td>
                      <div className="row-actions">
                        <button className="button button--small button--outline" disabled={Boolean(busy)} onClick={() => editNote(source)}>
                          <NotePencil size={16} />Ghi chú
                        </button>
                        <button className="button button--small button--danger" disabled={Boolean(busy) || !source.vector_count} onClick={() => previewVectorDelete(source)}>
                          <Trash size={16} />Vector
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {sources.data?.items?.length && viewMode === "card" ? (
          <div className="knowledge-source-cards">
            {sources.data.items.map((source) => {
              const expanded = expandedSources.has(source.chat_id);
              const storageBytes =
                (source.message_storage_bytes || 0) +
                (source.media_storage_bytes || 0);
              return (
                <article className="knowledge-source-card" key={source.chat_id}>
                  <header>
                    <input
                      type="checkbox"
                      aria-label={`Chọn ${source.title || source.chat_id}`}
                      checked={selected.has(source.chat_id)}
                      onChange={(event) =>
                        toggleSourceSelection(source.chat_id, event.target.checked)
                      }
                    />
                    <div>
                      <b>{source.title || "Chưa có tên"}</b>
                      <small>{source.chat_id} · {source.chat_type}</small>
                    </div>
                    <Badge tone={statusTone(source.status)}>
                      {humanize(source.status)}
                    </Badge>
                  </header>
                  <dl>
                    <div><dt>SQLite</dt><dd>{formatNumber(source.mysql_message_count)}</dd></div>
                    <div><dt>Vector</dt><dd>{formatNumber(source.vector_count)}</dd></div>
                    <div><dt>Dung lượng</dt><dd>{formatBytes(storageBytes)}</dd></div>
                  </dl>
                  {expanded ? (
                    <div className="knowledge-card-detail">
                      <dl>
                        <div><dt>Tin có nội dung</dt><dd>{formatNumber(source.text_message_count)}</dd></div>
                        <div><dt>Lần học</dt><dd>{formatDate(source.last_learned_at)}</dd></div>
                        <div><dt>Tự học</dt><dd>{source.auto_knowledge ? "Đang bật" : "Đang tắt"}</dd></div>
                      </dl>
                      <div className="knowledge-card-note">
                        <span>Lỗi / ghi chú</span>
                        <p>{source.last_error || source.owner_note || source.status_reason || "Không có"}</p>
                      </div>
                      <div className="row-actions">
                        <button
                          className="button button--small button--outline"
                          disabled={Boolean(busy)}
                          onClick={() => editNote(source)}
                        >
                          <NotePencil size={16} />Ghi chú
                        </button>
                        <button
                          className="button button--small button--danger"
                          disabled={Boolean(busy) || !source.vector_count}
                          onClick={() => previewVectorDelete(source)}
                        >
                          <Trash size={16} />Preview xóa vector
                        </button>
                      </div>
                    </div>
                  ) : null}
                  <button
                    className="knowledge-card-toggle"
                    aria-expanded={expanded}
                    onClick={() => toggleSourceExpanded(source.chat_id)}
                  >
                    {expanded ? <CaretUp size={18} /> : <CaretDown size={18} />}
                    {expanded ? "Thu gọn" : "Mở chi tiết"}
                  </button>
                </article>
              );
            })}
          </div>
        ) : null}
        {!sources.data?.items?.length && sources.data ? (
          <EmptyState
            title="Không có nguồn phù hợp"
            description="Không có nguồn nào ở trạng thái đã chọn."
          />
        ) : null}
        {sources.data ? (
          <div className="table-footer">
            <span>
              Trang {page}/{Math.max(1, sources.data.pages)} · {formatNumber(sources.data.total)} nguồn
            </span>
            <label className="page-size-control">
              <span>Số dòng</span>
              <select value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value)); setPage(1); }}>
                {[10, 25, 50, 100].map((value) => <option key={value}>{value}</option>)}
              </select>
            </label>
            <div className="pagination">
              <button disabled={page <= 1} onClick={() => setPage((value) => value - 1)}>
                <CaretLeft size={18} />
                Trước
              </button>
              <button
                disabled={page >= Math.max(1, sources.data.pages)}
                onClick={() => setPage((value) => value + 1)}
              >
                Sau
                <CaretRight size={18} />
              </button>
            </div>
          </div>
        ) : null}
      </section>

      <section className="panel">
        <PanelHeader
          eyebrow="BACKGROUND JOBS · LIVE"
          title="Tiến trình học"
          action={<Badge tone="paper">{jobs.data?.items?.length || 0} JOB</Badge>}
        />
        {jobs.loading && !jobs.data ? <LoadingState label="Đang tải learning job…" /> : null}
        {jobs.error && !jobs.data ? <ErrorState error={jobs.error} onRetry={jobs.reload} /> : null}
        {normalizedJobs.length ? (
          <div className="learning-job-list">
            {normalizedJobs.map((job) => (
              <article className="learning-job-card" key={job.id}>
                <header>
                  <div>
                    <small>JOB {job.id}</small>
                    <h3>{job.payload?.title || job.payload?.chat_id || "Nguồn Telegram"}</h3>
                  </div>
                  <Badge tone={statusTone(job.status)}>{jobLabel(job.status)}</Badge>
                </header>
                <div className="learning-job-progress">
                  <div>
                    <span>{jobLabel(job.phase)}</span>
                    {job.progress != null ? (
                      <b>{job.progress}%</b>
                    ) : (
                      <b>Không có dữ liệu tiến độ</b>
                    )}
                  </div>
                  {job.progress != null ? (
                    <span
                      className="job-progress"
                      role="progressbar"
                      aria-label={`Tiến độ ${job.progress}%`}
                      aria-valuemin="0"
                      aria-valuemax="100"
                      aria-valuenow={job.progress}
                    >
                      <i style={{ width: `${job.progress}%` }} />
                    </span>
                  ) : null}
                </div>
                <dl className="learning-job-metrics">
                  <div>
                    <dt>Processed / total</dt>
                    <dd>
                      {job.processed != null && job.total != null
                        ? `${formatNumber(job.processed)} / ${formatNumber(job.total)}`
                        : "—"}
                    </dd>
                  </div>
                  <div><dt>Tin đã sync</dt><dd>{formatNumber(job.synced_messages)}</dd></div>
                  <div><dt>Vector đã tạo</dt><dd>{formatNumber(job.vectors_created)}</dd></div>
                  <div><dt>New / reused</dt><dd>{formatNumber(job.indexed_new)} / {formatNumber(job.reused_existing)}</dd></div>
                  <div><dt>Filtered / duplicate / skipped</dt><dd>{formatNumber(job.filtered)} / {formatNumber(job.duplicate)} / {formatNumber(job.skipped)}</dd></div>
                  <div><dt>Vectors before / after</dt><dd>{formatNumber(job.vectors_before)} / {formatNumber(job.vectors_after)}</dd></div>
                  <div><dt>Thời lượng</dt><dd>{formatDuration(job.duration_ms)}</dd></div>
                  <div><dt>Cập nhật</dt><dd>{formatDate(job.updated_at)}</dd></div>
                  <div><dt>Attempts</dt><dd>{job.attempts}/{job.max_attempts}</dd></div>
                </dl>
                <div className="learning-job-error">
                  <span>Lỗi gần nhất</span>
                  <p>{job.last_error || "Không có"}</p>
                </div>
                <footer>
                  {["queued", "running"].includes(job.status) ? (
                    <button
                      className="button button--outline"
                      disabled={Boolean(busy)}
                      onClick={() => changeJob(job, "pause")}
                    >
                      <Pause size={16} />Tạm dừng
                    </button>
                  ) : null}
                  {job.status === "paused" ? (
                    <button
                      className="button button--outline"
                      disabled={Boolean(busy)}
                      onClick={() => changeJob(job, "resume")}
                    >
                      <Play size={16} />Tiếp tục
                    </button>
                  ) : null}
                  {job.status === "failed" ? (
                    <button
                      className="button button--outline"
                      disabled={Boolean(busy)}
                      onClick={() => changeJob(job, "retry")}
                    >
                      <ArrowsClockwise size={16} />Thử lại
                    </button>
                  ) : null}
                </footer>
              </article>
            ))}
          </div>
        ) : jobs.data ? (
          <EmptyState title="Chưa có learning job" description="Bật học từ trang chi tiết nguồn." />
        ) : null}
      </section>
    </section>
  );
}
