import {
  ArrowsClockwise,
  CheckCircle,
  Warning,
  X,
} from "@phosphor-icons/react";

export function Badge({ children, tone = "ink", icon: Icon }) {
  return (
    <span className={`badge badge--${tone}`}>
      {Icon ? <Icon weight="fill" aria-hidden="true" /> : null}
      {children}
    </span>
  );
}

export function IconButton({ label, children, className = "", ...props }) {
  return (
    <button
      type="button"
      className={`icon-button ${className}`}
      aria-label={label}
      title={label}
      {...props}
    >
      {children}
    </button>
  );
}

export function PanelHeader({ eyebrow, title, action }) {
  return (
    <div className="panel-heading">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h2>{title}</h2>
      </div>
      {action}
    </div>
  );
}

export function Toggle({ active, onChange, label, disabled = false }) {
  return (
    <button
      type="button"
      className={`ops-toggle ${active ? "is-on" : ""}`}
      role="switch"
      aria-checked={active}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!active)}
    >
      <span />
      <b>{active ? "ON" : "OFF"}</b>
    </button>
  );
}

export function LoadingState({ label = "Đang tải dữ liệu…" }) {
  return (
    <div className="empty-state live-state" role="status">
      <ArrowsClockwise className="spin" size={42} weight="bold" />
      <b>{label}</b>
      <span>Dashboard đang đọc dữ liệu từ Admin API local.</span>
    </div>
  );
}

export function ErrorState({ error, onRetry }) {
  return (
    <div className="empty-state live-state live-state--error" role="alert">
      <Warning size={42} weight="fill" />
      <b>Không tải được dữ liệu</b>
      <span>{error?.message || "Đã xảy ra lỗi không xác định."}</span>
      {onRetry ? (
        <button className="button button--outline" onClick={onRetry}>
          <ArrowsClockwise size={18} weight="bold" />
          Thử lại
        </button>
      ) : null}
    </div>
  );
}

export function EmptyState({
  title = "Chưa có dữ liệu",
  description = "Không có bản ghi phù hợp với bộ lọc hiện tại.",
}) {
  return (
    <div className="empty-state live-state">
      <CheckCircle size={42} weight="bold" />
      <b>{title}</b>
      <span>{description}</span>
    </div>
  );
}

export function Toast({ toast, onClose }) {
  if (!toast?.message) return null;
  return (
    <div
      className={`toast ${toast.tone === "error" ? "toast--error" : ""}`}
      role={toast.tone === "error" ? "alert" : "status"}
      aria-live="polite"
      aria-atomic="true"
    >
      {toast.tone === "error" ? (
        <Warning size={24} weight="fill" />
      ) : (
        <CheckCircle size={24} weight="fill" />
      )}
      <span>{toast.message}</span>
      <IconButton label="Đóng thông báo" onClick={onClose}>
        <X size={18} weight="bold" />
      </IconButton>
    </div>
  );
}

export function Meter({ value, tone = "teal" }) {
  const bounded = Math.max(0, Math.min(100, Number(value || 0)));
  return (
    <span className="ops-meter" aria-label={`${bounded}%`}>
      <i className={`ops-meter--${tone}`} style={{ width: `${bounded}%` }} />
    </span>
  );
}
