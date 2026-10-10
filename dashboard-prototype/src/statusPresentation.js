const labels = { checking: "ĐANG KIỂM TRA", ready: "SẴN SÀNG", degraded: "SUY GIẢM", disconnected: "CHƯA KẾT NỐI", unknown: "CHƯA BIẾT" };

export function connectionPresentation(row, now = Date.now(), unavailable = false) {
  const checkedAt = typeof row?.checked_at === "string" ? Date.parse(row.checked_at) : NaN;
  const measured = Number.isFinite(checkedAt) && checkedAt <= now;
  const reported = row?.state ?? (row?.status === "ok" ? "ready" : row?.status);
  const state = Object.hasOwn(labels, reported) && (reported !== "ready" || measured) ? reported : "unknown";
  const stale = Boolean(row) && (unavailable || (measured && now - checkedAt >= 60000));
  const online = state === "ready" && !stale;
  return { state, stale, online, label: `${stale ? "DỮ LIỆU CŨ · " : ""}${labels[state]}`, tone: online ? "success" : "yellow" };
}

export function formatPercent(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? `${value.toFixed(1)}%` : "Chưa biết";
}
