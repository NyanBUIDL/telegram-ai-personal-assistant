import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bell,
  Brain,
  ChartBar,
  Cloud,
  Command,
  Cpu,
  FileText,
  HardDrives,
  Lightning,
  List,
  PaperPlaneTilt,
  RadioButton,
  Robot,
  ShieldCheck,
  SignOut,
  TerminalWindow,
  UsersThree,
  Wrench,
  X,
} from "@phosphor-icons/react";

import {
  api,
  connectEvents,
  onAuthFailure,
  setCsrfToken,
} from "./api.js";
import { formatBytes, formatDate, humanize } from "./format.js";
import { useDialogA11y } from "./useDialogA11y.js";
import { AiView, ModelsView } from "./views/AiViews.jsx";
import { GroupDetailView, GroupsView } from "./views/GroupsView.jsx";
import { KnowledgeView } from "./views/KnowledgeView.jsx";
import { FirstSourceAssistant } from "./components/FirstSourceAssistant.jsx";
import {
  ActionsView,
  AuditView,
  StorageView,
  WorkersView,
} from "./views/OperationsViews.jsx";
import { OverviewView } from "./views/OverviewView.jsx";
import { SetupDashboard, SetupReadiness, primaryNavigation } from "./views/OnboardingView.jsx";
import {
  ConnectionsView,
  DocumentationView,
  PolicyView,
  SecurityView,
  TelegramFeaturesView,
} from "./views/SystemViews.jsx";
import { Badge, IconButton, Toast } from "./ui.jsx";

const featureNavigation = [
  { id: "overview", label: "Tổng quan", icon: ChartBar, section: "VẬN HÀNH" },
  { id: "connections", label: "Kết nối", icon: RadioButton },
  { id: "groups", label: "Nhóm Telegram", icon: UsersThree },
  { id: "policy", label: "Policy Engine", icon: ShieldCheck },
  { id: "ai-rag", label: "AI & RAG", icon: Cloud, section: "AI & DỮ LIỆU" },
  { id: "models", label: "Local Models", icon: Cpu },
  { id: "knowledge", label: "Kho tri thức", icon: Brain },
  { id: "storage", label: "Bộ nhớ & lưu trữ", icon: HardDrives },
  { id: "actions", label: "Hành động chờ", icon: Lightning, section: "KIỂM SOÁT" },
  { id: "workers", label: "Scheduler & Workers", icon: Wrench },
  { id: "audit", label: "Audit Log", icon: TerminalWindow },
  { id: "security", label: "Bảo mật", icon: ShieldCheck },
  { id: "documentation", label: "Tài liệu & Hệ thống", icon: FileText },
  {
    id: "telegram-features",
    label: "Chức năng Telegram",
    icon: PaperPlaneTilt,
    section: "TRỢ LÝ CHÍNH",
  },
];

const navigation = primaryNavigation.map(item => ({ ...item, icon: featureNavigation.find(feature => feature.id === item.id).icon }));
const advancedNavigation = featureNavigation.filter(item => !navigation.some(primary => primary.id === item.id));
const ACTION_LABELS = {
  set_chat_allowed: "Thay đổi trạng thái nguồn",
  set_chat_permission: "Thay đổi quyền nguồn",
  apply_permission_template: "Áp dụng bộ quyền",
  setup_moderation: "Thiết lập kiểm duyệt",
  set_link_spam_auto_moderation: "Bật/tắt tự xóa link",
  set_group_ai_ask: "Bật/tắt AI trong group",
  sync_chat_history: "Đồng bộ lịch sử Telegram",
  backfill_chat_history: "Quét toàn bộ lịch sử Telegram",
  delete_history_link_posts: "Xóa hàng loạt post lịch sử",
  enable_group_learning: "Học một nguồn",
  enable_group_learning_bulk: "Học nhiều nguồn",
  leave_telegram_chat: "Rời group/channel",
  delete_learned_data: "Xóa dữ liệu đã học",
  storage_cleanup: "Dọn dẹp lưu trữ",
  delete_ollama_model: "Xóa model Ollama",
};

function LoginScreen({ error = "" }) {
  return (
    <main className="login-shell">
      <section className="login-poster">
        <div className="brand login-brand">
          <div className="brand-mark"><Robot size={31} weight="fill" /></div>
          <div><strong>TELEGRAM//AI</strong><span>ADMIN CONSOLE</span></div>
        </div>
        <div className="login-copy">
          <p className="eyebrow">LOCAL OWNER ACCESS</p>
          <h1>Đăng nhập<br />bảng vận hành.</h1>
          <p>Dashboard chỉ hoạt động trên máy cục bộ. Secret và session Telegram không được gửi tới trình duyệt.</p>
        </div>
        <div className="login-security-strip"><span>LOOPBACK ONLY</span><span>HTTPONLY COOKIE</span><span>CSRF PROTECTED</span></div>
      </section>
      <section className="login-panel">
        <div className="login-form">
          <div className="login-icon"><ShieldCheck size={42} weight="fill" /></div>
          <p className="eyebrow">OWNER AUTHENTICATION</p>
          <h2>Mở từ ứng dụng Windows</h2>
          <p>Mở Telegram AI trên Windows và bấm “Mở dashboard”. Vé đăng nhập dùng một lần và hết hạn sau 30 giây.</p>
          {error ? <div className="login-error" role="alert">{error}</div> : null}
          <button className="button button--primary login-submit" onClick={() => window.location.reload()}>
            <ShieldCheck size={20} weight="bold" />Kiểm tra lại phiên
          </button>
          <small>Không nhập API key, OTP Telegram hoặc mật khẩu vào trình duyệt.</small>
        </div>
      </section>
    </main>
  );
}

function SessionLoading() {
  return (
    <main className="session-loading">
      <div className="brand">
        <div className="brand-mark"><Robot size={29} weight="fill" /></div>
        <div><strong>TELEGRAM//AI</strong><span>ĐANG KIỂM TRA PHIÊN OWNER</span></div>
      </div>
      <span className="session-loading-bar" />
    </main>
  );
}

function ReviewModal({ action, onClose, onDecision, busy }) {
  const dialogRef = useDialogA11y(Boolean(action), onClose);
  if (!action) return null;
  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <div
        ref={dialogRef}
        className="modal review-modal modal-shell"
        role="dialog"
        aria-modal="true"
        aria-labelledby="review-title"
        aria-describedby="review-description"
        tabIndex={-1}
      >
        <header className="modal-header">
          <div>
            <div className="modal-flag modal-flag--yellow">
              <ShieldCheck size={22} weight="fill" />
              {action.status === "pending" ? "OWNER CONFIRMATION" : "ACTION DETAIL"}
            </div>
            <p className="eyebrow">{action.action_id}</p>
            <h2 id="review-title">
              {ACTION_LABELS[action.action_type] || humanize(action.action_type)}
            </h2>
          </div>
          <IconButton label="Đóng" className="modal-close" onClick={onClose}>
            <X size={24} weight="bold" />
          </IconButton>
        </header>
        <div className="modal-body" id="review-description">
          <div className="review-summary">
            <div><span>Trạng thái</span><Badge tone="yellow">{humanize(action.status)}</Badge></div>
            <div><span>Phạm vi</span><b>{action.chat_id || "Hệ thống local"}</b></div>
            <div><span>Hết hạn</span><b>{formatDate(action.expires_at)}</b></div>
          </div>
          <div className="review-preview">
            <span>Preview từ backend</span>
            <p>{action.preview || "Backend không cung cấp nội dung preview."}</p>
          </div>
          {action.reason ? (
            <div className="review-warning">
              <ShieldCheck size={22} weight="fill" />
              <p><b>Lý do / cảnh báo</b>{action.reason}</p>
            </div>
          ) : null}
          {action.payload ? (
            <details className="review-payload">
              <summary>Payload đã ẩn secret</summary>
              <pre>{JSON.stringify(action.payload, null, 2)}</pre>
            </details>
          ) : null}
          {action.error ? <div className="login-error">{action.error}</div> : null}
        </div>
        <footer className="modal-actions modal-footer">
          {action.status === "pending" ? (
            <>
              <button
                className="button button--outline"
                disabled={busy}
                onClick={() => onDecision("cancel", action)}
              >
                Hủy yêu cầu
              </button>
              <button
                className="button button--primary"
                disabled={busy}
                onClick={() => onDecision("confirm", action)}
              >
                <ShieldCheck size={18} weight="bold" />
                {busy ? "Đang xử lý…" : "Xác nhận thực thi"}
              </button>
            </>
          ) : (
            <button className="button button--outline" onClick={onClose}>
              Đóng chi tiết
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}

export function App() {
  const [session, setSession] = useState(null);
  const [authState, setAuthState] = useState("checking");
  const [launchError, setLaunchError] = useState("");
  const [activePage, setActivePage] = useState("overview");
  const [selectedGroupId, setSelectedGroupId] = useState(null);
  const [mobileNav, setMobileNav] = useState(false);
  const [notifications, setNotifications] = useState(false);
  const [reviewAction, setReviewAction] = useState(null);
  const [reviewBusy, setReviewBusy] = useState(false);
  const [toast, setToast] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [snapshot, setSnapshot] = useState(null);
  const [realtimeState, setRealtimeState] = useState("connecting");
  const [lastRealtimeAt, setLastRealtimeAt] = useState(null);
  const [reconnectAttempt, setReconnectAttempt] = useState(0);
  const [eventConnectionKey, setEventConnectionKey] = useState(0);
  const snapshotSignature = useRef("");
  const toastTimer = useRef(null);

  const showToast = (message, tone = "success") => {
    window.clearTimeout(toastTimer.current);
    setToast({ message, tone });
    toastTimer.current = window.setTimeout(() => setToast(null), 4200);
  };

  const loseSession = () => {
    setCsrfToken("");
    setSession(null);
    setAuthState("anonymous");
    setReviewAction(null);
    setNotifications(false);
  };

  useEffect(() => {
    const removeHandler = onAuthFailure(loseSession);
    api
      .bootstrapSession()
      .then((value) => {
        setCsrfToken(value.csrf_token);
        setSession(value);
        setAuthState("authenticated");
      })
      .catch((error) => {
        if (error.status === 401) loseSession();
        else {
          setAuthState("anonymous");
          setLaunchError(error.message);
        }
      });
    return removeHandler;
  }, []);

  useEffect(() => {
    if (authState !== "authenticated" || session?.authority === "setup_only") return undefined;
    setRealtimeState(navigator.onLine ? "connecting" : "offline");
    return connectEvents({
      onOpen: () => {
        setRealtimeState("live");
        setReconnectAttempt(0);
      },
      onSnapshot: (value) => {
        setSnapshot(value);
        setRealtimeState("live");
        setLastRealtimeAt(value.sent_at || new Date().toISOString());
        const signature = JSON.stringify([
          value.pending_actions,
          value.jobs,
          value.runtime?.id,
          value.latest_audit_id,
        ]);
        if (snapshotSignature.current && snapshotSignature.current !== signature) {
          setRefreshKey((key) => key + 1);
        }
        snapshotSignature.current = signature;
      },
      onError: () => {
        setRealtimeState(navigator.onLine ? "reconnecting" : "offline");
        setReconnectAttempt((value) => value + 1);
      },
    });
  }, [authState, eventConnectionKey, session?.authority]);

  useEffect(() => {
    if (authState !== "authenticated" || session?.authority === "setup_only") return undefined;
    const handleOffline = () => setRealtimeState("offline");
    const handleOnline = () => {
      setRealtimeState("connecting");
      setEventConnectionKey((value) => value + 1);
    };
    window.addEventListener("offline", handleOffline);
    window.addEventListener("online", handleOnline);
    return () => {
      window.removeEventListener("offline", handleOffline);
      window.removeEventListener("online", handleOnline);
    };
  }, [authState, session?.authority]);

  useEffect(
    () => () => {
      window.clearTimeout(toastTimer.current);
    },
    [],
  );

  const handleLogout = async () => {
    try {
      await api.logout();
    } catch (error) {
      if (error.status !== 401) showToast(error.message, "error");
    } finally {
      loseSession();
    }
  };

  const handleReviewDecision = async (decision, action) => {
    setReviewBusy(true);
    try {
      if (decision === "confirm") await api.confirmAction(action.action_id);
      else await api.cancelAction(action.action_id);
      setReviewAction(null);
      showToast(
        decision === "confirm"
          ? "Owner đã xác nhận. Worker sẽ kiểm tra lại và thực thi."
          : "Đã hủy PendingAction.",
      );
      setRefreshKey((key) => key + 1);
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      setReviewBusy(false);
    }
  };

  const openGroup = (chatId) => {
    setSelectedGroupId(chatId);
    setActivePage("group-detail");
    setMobileNav(false);
  };

  const switchPage = (id) => {
    setActivePage(id);
    setMobileNav(false);
    setNotifications(false);
  };

  const retryRealtime = () => {
    if (!navigator.onLine) {
      setRealtimeState("offline");
      showToast("Máy đang offline. Hãy kiểm tra kết nối mạng.", "error");
      return;
    }
    setRealtimeState("connecting");
    setEventConnectionKey((value) => value + 1);
  };

  const activeLabel = useMemo(() => {
    if (activePage === "group-detail") return "Chi tiết nhóm";
    return [...navigation, ...advancedNavigation].find((item) => item.id === activePage)?.label || "Tổng quan";
  }, [activePage]);

  if (authState === "checking") return <SessionLoading />;
  if (authState === "authenticated" && session?.authority === "setup_only") {
    return <SetupDashboard session={session} onLogout={handleLogout} />;
  }
  if (authState !== "authenticated") return <LoginScreen error={launchError} />;

  const pendingCount = snapshot?.pending_actions || 0;
  const queuedJobs = snapshot?.jobs?.queued || 0;
  const runtime = snapshot?.runtime;

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? "is-open" : ""}`}>
        <div className="brand">
          <div className="brand-mark"><Robot size={29} weight="fill" /></div>
          <div><strong>TELEGRAM//AI</strong><span>ADMIN CONSOLE</span></div>
        </div>
        <nav aria-label="Điều hướng chính">
          {navigation.map((item) => {
            const Icon = item.icon;
            const active =
              activePage === item.id ||
              (activePage === "group-detail" && item.id === "groups");
            const count =
              item.id === "actions"
                ? pendingCount
                : item.id === "knowledge"
                  ? queuedJobs
                  : null;
            return (
              <div className="nav-entry" key={item.id}>
                {item.section ? <p className="nav-label">{item.section}</p> : null}
                <button className={active ? "is-active" : ""} onClick={() => switchPage(item.id)}>
                  <Icon size={20} weight={active ? "fill" : "bold"} />
                  <span>{item.label}</span>
                  {count ? <b>{count}</b> : null}
                </button>
              </div>
            );
          })}
        </nav>
        <details className="advanced-navigation"><summary>Chức năng nâng cao</summary><nav aria-label="Điều hướng nâng cao">{advancedNavigation.map(item => <button key={item.id} className={activePage === item.id ? "is-active" : ""} onClick={() => switchPage(item.id)}>{item.label}</button>)}</nav></details>
        <div className="sidebar-rule" />
        <div className="owner-card">
          <div className="owner-avatar">OW</div>
          <div>
            <b>Owner {session.owner_id}</b>
            <span>
              Local ·{" "}
              {realtimeState === "live"
                ? "Realtime"
                : realtimeState === "offline"
                  ? "Offline"
                  : "Đang kết nối"}
            </span>
          </div>
          <IconButton label="Đăng xuất" onClick={handleLogout}>
            <SignOut size={21} weight="bold" />
          </IconButton>
        </div>
        <div className="runtime-stamp">
          <span>RUNTIME</span>
          <b>v0.1.0</b>
          <i className={realtimeState === "live" ? "" : "is-warning"} />
        </div>
      </aside>

      {mobileNav ? (
        <button className="nav-scrim" aria-label="Đóng menu" onClick={() => setMobileNav(false)} />
      ) : null}

      <main className="main">
        <header className="topbar">
          <div className="topbar-title">
            <IconButton
              label="Mở menu"
              className="menu-button"
              onClick={() => setMobileNav(true)}
            >
              <List size={25} weight="bold" />
            </IconButton>
            <div>
              <p>TELEGRAM AI / <b>{activeLabel.toUpperCase()}</b></p>
              <h1>{activeLabel}</h1>
            </div>
          </div>
          <div className="topbar-actions">
            <Badge
              tone={
                realtimeState === "live"
                  ? "success"
                  : realtimeState === "offline"
                    ? "magenta"
                    : "yellow"
              }
            >
              {realtimeState === "live"
                ? "LIVE"
                : realtimeState === "offline"
                  ? "OFFLINE"
                  : realtimeState === "connecting"
                    ? "CONNECTING"
                    : "RECONNECTING"}
            </Badge>
            <div className="notification-wrap">
              <IconButton
                label="Thông báo"
                className="notification-button"
                onClick={() => setNotifications((open) => !open)}
              >
                <Bell size={23} weight="bold" />
                {pendingCount || queuedJobs ? <span className="notification-dot" /> : null}
              </IconButton>
              {notifications ? (
                <div className="notification-popover">
                  <div>
                    <b>Trạng thái realtime</b>
                    <Badge tone={realtimeState === "live" ? "success" : "yellow"}>
                      {realtimeState.toUpperCase()}
                    </Badge>
                  </div>
                  <p>{pendingCount} hành động đang chờ owner.</p>
                  <p>{queuedJobs} background job đang trong queue.</p>
                  <p>RAM runtime: {formatBytes(runtime?.rss_bytes)}</p>
                  <p>Cập nhật thành công: {formatDate(lastRealtimeAt)}</p>
                </div>
              ) : null}
            </div>
            <button className="button button--primary" onClick={() => switchPage("actions")}>
              <Command size={20} weight="bold" />
              Hành động chờ
            </button>
          </div>
        </header>

        {realtimeState !== "live" ? (
          <section className={`realtime-warning realtime-warning--${realtimeState}`} role="status">
            <div>
              <b>
                {realtimeState === "offline"
                  ? "Dashboard đang offline"
                  : realtimeState === "connecting"
                    ? "Đang kết nối realtime"
                    : "Mất kết nối SSE, đang thử lại"}
              </b>
              <span>
                {lastRealtimeAt
                  ? `Snapshot gần nhất: ${formatDate(lastRealtimeAt)}. Dữ liệu có thể đã cũ.`
                  : "Chưa nhận được snapshot realtime đáng tin cậy."}
              </span>
            </div>
            <button className="button button--outline" onClick={retryRealtime}>
              Thử kết nối lại{reconnectAttempt ? ` · lần ${reconnectAttempt}` : ""}
            </button>
          </section>
        ) : null}

        <div className="content">
          {["overview", "groups", "knowledge"].includes(activePage) ? <FirstSourceAssistant session={session} refreshKey={refreshKey} onOpenGroup={openGroup} /> : null}
          {["overview", "connections"].includes(activePage) ? <SetupReadiness session={session} /> : null}
          {activePage === "overview" ? (
            <OverviewView
              refreshKey={refreshKey}
              onReview={setReviewAction}
              onNavigate={switchPage}
            />
          ) : null}
          {activePage === "connections" ? (
            <ConnectionsView
              refreshKey={refreshKey}
              realtimeState={realtimeState}
              lastRealtimeAt={lastRealtimeAt}
              reconnectAttempt={reconnectAttempt}
              onRetry={retryRealtime}
            />
          ) : null}
          {activePage === "groups" ? (
            <GroupsView refreshKey={refreshKey} onOpenGroup={openGroup} />
          ) : null}
          {activePage === "group-detail" && selectedGroupId ? (
            <GroupDetailView
              chatId={selectedGroupId}
              refreshKey={refreshKey}
              onBack={() => switchPage("groups")}
              onCreatedAction={setReviewAction}
              onToast={showToast}
            />
          ) : null}
          {activePage === "policy" ? <PolicyView /> : null}
          {activePage === "ai-rag" ? (
            <AiView refreshKey={refreshKey} onToast={showToast} />
          ) : null}
          {activePage === "models" ? (
            <ModelsView
              refreshKey={refreshKey}
              onCreatedAction={setReviewAction}
              onToast={showToast}
            />
          ) : null}
          {activePage === "knowledge" ? (
            <KnowledgeView
              refreshKey={refreshKey}
              onCreatedAction={setReviewAction}
              onToast={showToast}
            />
          ) : null}
          {activePage === "storage" ? (
            <StorageView
              refreshKey={refreshKey}
              onCreatedAction={setReviewAction}
              onToast={showToast}
            />
          ) : null}
          {activePage === "actions" ? (
            <ActionsView refreshKey={refreshKey} onReview={setReviewAction} />
          ) : null}
          {activePage === "workers" ? <WorkersView refreshKey={refreshKey} /> : null}
          {activePage === "audit" ? (
            <AuditView refreshKey={refreshKey} onToast={showToast} />
          ) : null}
          {activePage === "security" ? <SecurityView /> : null}
          {activePage === "documentation" ? (
            <DocumentationView refreshKey={refreshKey} />
          ) : null}
          {activePage === "telegram-features" ? <TelegramFeaturesView /> : null}
        </div>
      </main>

      <ReviewModal
        action={reviewAction}
        busy={reviewBusy}
        onClose={() => !reviewBusy && setReviewAction(null)}
        onDecision={handleReviewDecision}
      />
      <Toast toast={toast} onClose={() => setToast(null)} />
    </div>
  );
}
