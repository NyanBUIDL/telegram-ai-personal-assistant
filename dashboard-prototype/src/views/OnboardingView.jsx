import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { Badge } from "../ui.jsx";
import { formatDate } from "../format.js";
import { contractSchema } from "../contracts/generated.js";
import { useObservedAt } from "../hooks.js";
import { connectionPresentation } from "../statusPresentation.js";

export const primaryNavigation = [
  { id: "overview", label: "Tổng quan" }, { id: "connections", label: "Kết nối" },
  { id: "groups", label: "Nguồn Telegram" }, { id: "knowledge", label: "Tri thức" },
  { id: "actions", label: "Công việc" }, { id: "workers", label: "Vận hành" },
  { id: "documentation", label: "Cài đặt & trợ giúp" },
];
const services = { runtime: "Ứng dụng", telegram_account: "Tài khoản Telegram", control_bot: "Bot điều khiển", chat_ai: "AI trò chuyện", embeddings: "Chỉ mục AI", storage: "Lưu trữ" };
const dialogs = [
  ["open_connection_dialog", "Mở cấu hình AI trên Windows"],
  ["open_telegram_login", "Đăng nhập Telegram trên Windows"],
  ["open_bot_dialog", "Kết nối bot trên Windows"],
];

export function SetupReadiness({ session }) {
  return <ProfileReadiness key={session.profile_id} session={session} />;
}

function ProfileReadiness({ session }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [operation, setOperation] = useState("");
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const observedAt = useObservedAt();
  const commandAbort = useRef(null);
  useEffect(() => {
    let alive = true;
    let controller;
    let timeout;
    let pending = false;
    const load = async () => {
      if (pending) return;
      pending = true;
      controller = new AbortController();
      timeout = setTimeout(() => controller.abort(), 10000);
      try {
        const [status, rows, availability] = await Promise.all([api.setupStatus(controller.signal), api.connections(controller.signal), api.nativeDialogs(controller.signal)]);
        const commandNames = contractSchema.contracts.NativeCommand.$defs.NativeCommandName.enum;
        if (status.profile.profile_id !== session.profile_id || availability?.profile_id !== session.profile_id || !Array.isArray(availability.commands) || availability.commands.some(name => !commandNames.includes(name)) || Object.keys(services).some(service => rows.filter(row => row.service === service).length !== 1) || rows.length !== 6) throw new Error("Invalid profile or service set");
        if (alive) { setData({ status, rows, commands: availability.commands }); setError(""); }
      } catch {
        if (alive) setError("Dữ liệu thiết lập không khả dụng. Kết quả đã lưu là dữ liệu cũ. Mở lại ứng dụng Windows hoặc thử kiểm tra lại.");
      } finally { clearTimeout(timeout); pending = false; }
    };
    load();
    const focus = () => { if (document.visibilityState === "visible") load(); };
    const interval = setInterval(focus, 15000);
    window.addEventListener("focus", focus);
    document.addEventListener("visibilitychange", focus);
    return () => { alive = false; controller?.abort(); clearTimeout(timeout); clearInterval(interval); window.removeEventListener("focus", focus); document.removeEventListener("visibilitychange", focus); };
  }, [session.profile_id, refresh]);
  useEffect(() => () => { commandAbort.current?.abort(); commandAbort.current = null; }, []);
  const requestDialog = async name => {
    if (busy || error || !data?.commands.includes(name)) return;
    setBusy(true); setOperation("");
    const controller = new AbortController(); commandAbort.current = controller;
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const result = await api.nativeCommand(name, session.profile_id, controller.signal);
      if (commandAbort.current !== controller) return;
      setOperation(result.state === "queued" ? "Yêu cầu đã xếp hàng trên Windows. Chưa xác nhận hộp thoại đã mở, kết nối thành công hoặc hoàn tất thiết lập. Quay lại ứng dụng Windows; yêu cầu chờ tối đa 30 giây rồi hết hạn nếu chưa được nhận." : `${result.state}: ${result.message}. ${result.next_action || "Kiểm tra lại trên Windows."}`);
    } catch (failure) {
      if (commandAbort.current !== controller) return;
      setOperation(failure.status === 409 ? "Trình xử lý Windows không sẵn sàng hoặc yêu cầu đã cũ. Mở lại ứng dụng Windows rồi thử lại." : "Không gửi được yêu cầu. Mở lại ứng dụng Windows rồi kiểm tra lại; chưa xác nhận kết nối.");
    } finally { clearTimeout(timeout); if (commandAbort.current === controller) { commandAbort.current = null; setBusy(false); setRefresh(value => value + 1); } }
  };
  return <section className="panel setup-readiness" aria-label="Thiết lập và trạng thái kết nối">
    <div className="panel-heading"><div><p className="eyebrow">THIẾT LẬP WINDOWS</p><h2>Tiếp tục thiết lập trên Windows</h2><p>Bước tiếp theo</p></div><button className="button button--outline" onClick={() => setRefresh(value => value + 1)}>Kiểm tra lại</button></div>
    {error ? <p role="alert">{error}</p> : <p>{data?.status.next_action || "Đang đọc trạng thái backend; chưa có xác nhận sẵn sàng."}</p>}
    <p>Nhập API key, token bot và thông tin đăng nhập Telegram trong cửa sổ Windows được mở từ các nút bên dưới.</p>
    <div className="setup-dialog-actions">{dialogs.map(([name, label]) => <div key={name}><button className="button button--primary" disabled={busy || Boolean(error) || !data?.commands.includes(name)} onClick={() => requestDialog(name)}>{label}</button>{error || !data?.commands.includes(name) ? <p>Trình xử lý này chưa sẵn sàng trên Windows.</p> : null}</div>)}</div>
    {operation ? <p role="status" className="realtime-warning">{operation}</p> : null}
    <div className="setup-services">{Object.entries(services).map(([service, title]) => {
      const row = data?.rows.find(value => value.service === service);
      const presentation = connectionPresentation(row, observedAt, Boolean(error));
      return <article className="setup-service" key={service}><h3>{title}</h3><Badge tone={presentation.tone}>{presentation.label}</Badge><p>{row?.message || "Chưa có kết quả kiểm tra."}</p><p>Kiểm tra: {row?.checked_at ? formatDate(row.checked_at) : "Chưa kiểm tra"}</p><p>{row?.next_action || "Bấm Kiểm tra lại hoặc mở ứng dụng Windows."}</p>{row?.capabilities.length ? <details className="setup-technical"><summary>Chi tiết kiểm tra</summary><p>Capability: {row.capabilities.join(", ")}. Thông tin cấu hình không chứng minh AI đã trả lời hoặc ứng dụng đã bật đủ chức năng.</p></details> : null}</article>;
    })}</div>
    <details className="setup-technical"><summary>Cách đọc trạng thái kết nối</summary><p>Kết quả kiểm tra quá một phút được ghi là dữ liệu cũ. Bấm Kiểm tra lại để đọc trạng thái hiện tại. Nút kết nối chỉ gửi yêu cầu mở cửa sổ Windows; hãy hoàn tất các bước trong cửa sổ đó. Bước thiết lập đã lưu không thay thế kết quả kiểm tra mới.</p></details>
    {data ? <p>Profile: {data.status.profile.profile_id} · Owner: {data.status.profile.owner_id ?? "Chưa ghép owner"} · Backend: {data.status.profile.storage_backend}</p> : null}
  </section>;
}

export function SetupDashboard({ session, onLogout }) {
  const [page, setPage] = useState("overview");
  const allowed = ["overview", "connections", "documentation"];
  return <main className="setup-dashboard"><header><p className="eyebrow">TELEGRAM//AI · PHIÊN THIẾT LẬP</p><h1>{primaryNavigation.find(item => item.id === page).label}</h1><button className="button button--outline" onClick={onLogout}>Đăng xuất</button></header><nav aria-label="Điều hướng chính" className="setup-navigation">{primaryNavigation.map(item => <div key={item.id}><button className="button button--outline" aria-current={page === item.id ? "page" : undefined} disabled={!allowed.includes(item.id)} onClick={() => setPage(item.id)}>{item.label}</button>{!allowed.includes(item.id) ? <p>Cần owner đã xác minh và ghép cặp.</p> : null}</div>)}</nav>{page === "documentation" ? <section className="panel"><h2>Tiếp tục thiết lập trên Windows</h2><p>Kết nối Telegram và bot, xác minh owner rồi ghép cặp trong ứng dụng Windows. Telegram là giao diện trợ lý chính. Mở dashboard lại từ Windows sau khi ghép cặp.</p><p>Tri thức là đồng bộ, làm sạch và lập chỉ mục dữ liệu được phép; không tự huấn luyện model. LOCAL ONLY giữ nội dung nguồn khỏi cloud chat và embedding. Học hoặc đổi index cần phạm vi và xác nhận owner.</p></section> : <SetupReadiness session={session} />}</main>;
}
