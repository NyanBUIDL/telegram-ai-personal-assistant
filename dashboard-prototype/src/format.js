export function formatNumber(value) {
  return new Intl.NumberFormat("vi-VN").format(Number(value || 0));
}

export function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(
    Math.floor(Math.log(Math.abs(bytes)) / Math.log(1024)),
    units.length - 1,
  );
  return `${(bytes / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`;
}

export function formatDate(value, options = {}) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("vi-VN", {
    dateStyle: options.dateStyle || "short",
    timeStyle: options.timeStyle || "short",
  }).format(new Date(value));
}

export function formatRelative(value) {
  if (!value) return "Chưa có";
  const delta = new Date(value).getTime() - Date.now();
  const formatter = new Intl.RelativeTimeFormat("vi-VN", { numeric: "auto" });
  const units = [
    ["day", 86_400_000],
    ["hour", 3_600_000],
    ["minute", 60_000],
  ];
  for (const [unit, size] of units) {
    if (Math.abs(delta) >= size || unit === "minute") {
      return formatter.format(Math.round(delta / size), unit);
    }
  }
  return "vừa xong";
}

export function statusTone(status) {
  const value = String(status || "").toLowerCase();
  if (
    ["success", "ok", "online", "completed", "executed", "running"].some((item) =>
      value.includes(item),
    )
  )
    return "success";
  if (
    ["fail", "error", "blocked", "denied", "cancelled", "expired"].some((item) =>
      value.includes(item),
    )
  )
    return "magenta";
  if (
    ["pending", "queued", "paused", "warning", "requested"].some((item) =>
      value.includes(item),
    )
  )
    return "yellow";
  return "teal";
}

export function humanize(value) {
  return String(value || "—")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}
