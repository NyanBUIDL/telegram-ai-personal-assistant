import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { canonicalChatId } from "../chatIds.js";
import { useDebouncedValue } from "../hooks.js";
import { Badge, PanelHeader } from "../ui.jsx";

const withdrawnStates = ["uncertain", "failed", "cancelled", "completed_with_warning"];
const metrics = [["synced_messages", "Tin đã sync"], ["indexed_new", "Đã tạo mới"], ["reused_existing", "Đã tái sử dụng"], ["filtered", "Đã lọc"], ["duplicate", "Trùng lặp"], ["skipped", "Bỏ qua"], ["failed", "Lỗi"]];
const count = value => typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? String(value) : "Chưa biết";

export function FirstSourceAssistant({ session, refreshKey, backgroundRefreshKey, onOpenGroup, onCreatedAction, onWithdrawAction, openReviewActionId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [choice, setChoice] = useState("");
  const [query, setQuery] = useState("");
  const [chatType, setChatType] = useState("");
  const [page, setPage] = useState(1);
  const [directory, setDirectory] = useState(null);
  const [directoryError, setDirectoryError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [previewNeedsRefresh, setPreviewNeedsRefresh] = useState(false);
  const epoch = useRef(0);
  const expected = useRef(undefined);
  const pending = useRef(null);
  const saving = useRef(false);
  const previewPending = useRef(null);
  const issuedAction = useRef(null);
  const openReview = useRef(openReviewActionId);
  openReview.current = openReviewActionId;
  const backgroundKey = useRef(backgroundRefreshKey);
  backgroundKey.current = backgroundRefreshKey;
  const loadedBackgroundKey = useRef(backgroundRefreshKey);
  const expiryTimer = useRef(null);
  const callbacks = useRef({ onCreatedAction, onWithdrawAction });
  callbacks.current = { onCreatedAction, onWithdrawAction };
  const debouncedQuery = useDebouncedValue(query);
  const withdrawPreview = useCallback(() => {
    previewPending.current?.abort(); previewPending.current = null;
    clearTimeout(expiryTimer.current);
    if (issuedAction.current) callbacks.current.onWithdrawAction?.(issuedAction.current);
    issuedAction.current = null;
    setPreviewBusy(false);
  }, []);

  useEffect(() => {
    if (session.authority !== "management") return;
    let alive = true;
    setDirectory(null); setDirectoryError("");
    api.groups({ query: debouncedQuery, chatType, page, pageSize: 10 }).then(value => {
      if (!Array.isArray(value.items)) throw new Error();
      value.items.forEach(row => canonicalChatId(row.chat_id));
      if (alive) setDirectory(value);
    }).catch(() => { if (alive) setDirectoryError("Không đọc được danh sách nguồn. Kiểm tra lại Admin API."); });
    return () => { alive = false; };
  }, [session.authority, session.profile_id, debouncedQuery, chatType, page, refreshKey, refresh]);

  useEffect(() => {
    expected.current = undefined; setChoice(""); setData(null);
  }, [session.profile_id]);

  useEffect(() => {
    if (session.authority !== "management") return;
    let alive = true;
    const load = async () => {
      if (saving.current) return;
      loadedBackgroundKey.current = backgroundKey.current;
      const current = ++epoch.current;
      withdrawPreview();
      pending.current?.abort();
      const controller = new AbortController(); pending.current = controller;
      const timeout = setTimeout(() => controller.abort(), 10000);
      setData(null); setError(""); setPreviewError("");
      try {
        const status = await api.firstSourceStatus(controller.signal);
        if (status.profile_id !== session.profile_id || (expected.current !== undefined && status.source_id !== expected.current)) throw new Error();
        const [group, jobs] = status.source_id ? await Promise.all([api.group(status.source_id, controller.signal), api.learningJobs({ signal: controller.signal })]) : [null, null];
        if (group && canonicalChatId(group.chat_id) !== status.source_id) throw new Error();
        if (!alive || current !== epoch.current || controller.signal.aborted) return;
        expected.current = status.source_id;
        setChoice(status.source_id || "");
        const job = jobs?.items?.find(row => String(row.id) === status.learning_operation?.operation_id && row.payload?.chat_id === status.source_id);
        setData({ status, group, job });
        setPreviewNeedsRefresh(false);
      } catch {
        if (alive && current === epoch.current) { setData(null); setError("Trạng thái nguồn không khả dụng hoặc đã thay đổi. Kiểm tra lại ứng dụng Windows."); }
      } finally { clearTimeout(timeout); }
    };
    load();
    const focus = () => {
      if (document.visibilityState === "visible") load();
      else { ++epoch.current; pending.current?.abort(); withdrawPreview(); setData(null); }
    };
    const interval = setInterval(() => {
      const guidedReviewOpen = issuedAction.current && openReview.current === issuedAction.current;
      if (!previewPending.current && !guidedReviewOpen) focus();
    }, 15000);
    window.addEventListener("focus", focus); document.addEventListener("visibilitychange", focus);
    return () => { alive = false; ++epoch.current; pending.current?.abort(); withdrawPreview(); clearInterval(interval); window.removeEventListener("focus", focus); document.removeEventListener("visibilitychange", focus); };
  }, [session.authority, session.profile_id, refreshKey, refresh, withdrawPreview]);

  useEffect(() => {
    const guidedReviewOpen = issuedAction.current && openReview.current === issuedAction.current;
    if (session.authority === "management" && loadedBackgroundKey.current !== backgroundRefreshKey && !saving.current && !previewPending.current && !guidedReviewOpen) setRefresh(value => value + 1);
  }, [backgroundRefreshKey, openReviewActionId, previewBusy, busy, session.authority]);

  const choose = value => {
    ++epoch.current; pending.current?.abort(); withdrawPreview(); setData(null); setError(""); setPreviewError("");
    expected.current = value || null; setChoice(value);
  };
  const save = async () => {
    if (!choice || busy) return;
    const current = ++epoch.current;
    withdrawPreview();
    pending.current?.abort();
    const controller = new AbortController(); pending.current = controller;
    const timeout = setTimeout(() => controller.abort(), 10000);
    saving.current = true; setBusy(true); setData(null); setError("");
    try {
      const status = await api.selectFirstSource(choice, controller.signal);
      if (status.profile_id !== session.profile_id || status.source_id !== choice) throw new Error();
      if (current === epoch.current && !controller.signal.aborted) setRefresh(value => value + 1);
    } catch {
      if (current === epoch.current) setError("Chưa xác nhận đã lưu nguồn. Kiểm tra trên Windows; chưa cấp quyền hay tạo công việc.");
    } finally { clearTimeout(timeout); saving.current = false; setBusy(false); }
  };
  const preview = async () => {
    if (!data?.status.source_id || data.status.source_id !== choice || busy || previewPending.current || previewNeedsRefresh) return;
    const source = data.status.source_id;
    const current = epoch.current;
    withdrawPreview();
    const controller = new AbortController(); previewPending.current = controller;
    const timeout = setTimeout(() => controller.abort(), 10000);
    setPreviewBusy(true); setPreviewError("");
    try {
      const action = await api.previewFirstSource(source, controller.signal);
      if (current !== epoch.current || controller.signal.aborted) return;
      issuedAction.current = action.action_id;
      expiryTimer.current = setTimeout(withdrawPreview, Math.min(Date.parse(action.expires_at) - Date.now(), 2147483647));
      callbacks.current.onCreatedAction?.(action);
    } catch {
      if (current === epoch.current) {
        setPreviewNeedsRefresh(true);
        setPreviewError("Trạng thái preview chưa xác định. Kiểm tra nguồn trước khi tạo preview mới; chưa xác nhận cấp quyền hoặc hoàn tất học.");
      }
    } finally {
      clearTimeout(timeout);
      if (previewPending.current === controller) { previewPending.current = null; setPreviewBusy(false); }
    }
  };
  if (session.authority !== "management") return null;
  const status = data?.status;
  const blocked = data?.group?.policy?.allowed !== true;
  const uncertain = [status?.learning_operation, status?.answer_operation].some(row => row && withdrawnStates.includes(row.state));
  const username = status?.bot_username;
  const safeUsername = typeof username === "string" && /^[A-Za-z0-9_]{1,32}$/.exec(username)?.[0] === username;
  const testAvailable = status?.source_id && !blocked && !uncertain && status.test_available === true && safeUsername;
  const learning = status?.learning_operation;
  return <section className="panel first-source-assistant" aria-label="Nguồn đầu tiên">
    <PanelHeader eyebrow="TRỢ LÝ TELEGRAM" title="Một nguồn, câu hỏi đầu tiên" action={<button className="button button--outline" disabled={busy} onClick={() => setRefresh(value => value + 1)}>Kiểm tra nguồn</button>} />
    <p>Chọn một nguồn làm ý định thiết lập; không tự ALLOW, cấp quyền hoặc học toàn bộ chat. Học là đồng bộ, làm sạch và lập chỉ mục; không tự huấn luyện model.</p>
    <div className="first-source-filters">
      <label>Tìm nguồn<input aria-label="Tìm nguồn" value={query} onChange={event => { setQuery(event.target.value); setPage(1); }} /></label>
      <label>Loại nguồn<select aria-label="Loại nguồn" value={chatType} onChange={event => { setChatType(event.target.value); setPage(1); }}><option value="">Tất cả</option><option value="supergroup">Supergroup</option><option value="group">Group</option><option value="channel">Channel</option></select></label>
      <label>Chọn một nguồn<select aria-label="Chọn một nguồn" value={choice} disabled={busy || !directory} onChange={event => choose(event.target.value)}><option value="">Chưa chọn</option>{choice && !directory?.items.some(row => row.chat_id === choice) ? <option value={choice}>{choice}</option> : null}{directory?.items.map(row => <option key={row.chat_id} value={row.chat_id}>{row.title || row.chat_id} · {row.chat_id}</option>)}</select></label>
    </div>
    {directoryError ? <p role="alert">{directoryError}</p> : null}
    <div className="first-source-actions"><button className="button button--outline" disabled={busy || page === 1} onClick={() => setPage(value => value - 1)}>Nguồn trước</button><span>Trang {page}</span><button className="button button--outline" disabled={busy || !directory || page * 10 >= directory.total} onClick={() => setPage(value => value + 1)}>Nguồn tiếp</button><button className="button button--primary" disabled={busy || !choice || !directory} onClick={save}>{busy ? "Đang lưu…" : "Lưu nguồn đã chọn"}</button></div>
    {error ? <p role="alert">{error}</p> : null}
    <p role="status">{status ? `${status.message} ${status.next_action || ""}` : "Chưa có trạng thái nguồn hiện tại; chưa xác nhận sẵn sàng."}</p>
    {status?.source_id ? <p>Nguồn đã lưu: {status.source_id} <button className="button button--outline" onClick={() => onOpenGroup?.(status.source_id)}>Quản lý quyền nâng cao</button></p> : null}
    <p>Chưa có preview quyền gắn với nguồn đã chọn.</p>
    <button className="button button--outline" disabled={busy} aria-disabled={previewBusy || previewNeedsRefresh || !status?.source_id || undefined} onClick={preview}>{previewBusy ? "Đang tạo preview…" : "Xem và xác nhận quyền"}</button>
    {previewError ? <p role="alert">{previewError}</p> : null}
    <p>Kiểm tra quyền tại nguồn. Phạm vi, quyền thay đổi, LOCAL ONLY, route cloud, consent và giới hạn học cần preview hiện tại cùng xác nhận owner trước khi thực thi.</p>
    {status?.source_id && blocked ? <p className="realtime-warning">BLOCK hoặc chưa xác định quyền: không dùng tiến độ cũ để bật hỏi thử. Kiểm tra quyền hiện tại.</p> : null}
    {uncertain ? <p className="realtime-warning">Cần đối chiếu kết quả chưa chắc chắn hoặc đã dừng. Kiểm tra trên Windows; không lặp lại lần gửi cũ để sửa kết quả.</p> : null}
    {learning && !blocked && !uncertain ? <article className="first-source-operation"><h3>Công việc của nguồn đã chọn</h3><Badge tone="yellow">{learning.state}</Badge><p>ID: {learning.operation_id} · {learning.message}</p><p>{learning.next_action}</p>{learning.progress === null ? <p>Chưa có dữ liệu tiến độ</p> : <><p>Tiến độ: {learning.progress}%</p><progress aria-label="Tiến độ nguồn đã chọn" max="100" value={learning.progress} aria-valuenow={learning.progress} /></>}{data.job ? <div><p>Đã xử lý / tổng: {count(data.job.payload?.processed)} / {count(data.job.payload?.total)}</p>{metrics.map(([field, label]) => <p key={field}>{label}: {count(data.job.payload?.[field])}</p>)}</div> : <p>Chưa có số liệu của đúng công việc và nguồn.</p>}</article> : null}
    {status?.answer_operation ? <p>Kết quả câu hỏi từ backend: {status.answer_operation.state} · {status.answer_operation.message} · {status.answer_operation.next_action}</p> : null}
    {testAvailable ? <div><p>Trong chat riêng với bot, owner gửi <code>/ask in:{status.source_id} câu hỏi của bạn</code>. Chỉ câu trả lời có trích dẫn nguồn và kết quả backend hiện tại mới xác minh bước này. Mở bot hoặc gửi câu hỏi chưa hoàn tất thiết lập.</p><a className="button button--primary" href={`https://t.me/${username}`} target="_blank" rel="noopener noreferrer">Mở bot để hỏi thử</a></div> : <p>Hỏi thử chưa khả dụng. Làm theo bước tiếp theo từ backend; không mở hộp thoại ghép bot để hỏi.</p>}
  </section>;
}
