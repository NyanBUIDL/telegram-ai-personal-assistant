const $ = (selector, scope = document) => scope.querySelector(selector);
const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];

const appState = {
  route: "summary",
  viewport: "desktop",
  renderState: "content",
  network: "online",
  priority: "models",
  comparison: "before",
  knowledgeView: "table",
  selectedSources: new Set(),
  jobState: "queued",
  connectionState: "live",
  downloadTimer: null,
  downloadProgress: 47,
  modalTrigger: null,
};

const priorityContent = {
  models: {
    image: "assets/before/10-local-models-desktop-1440x900.png",
    alt: "Ảnh audit trước khi sửa của màn Local Models",
    caption: "BEFORE · card metadata dính thành chuỗi, khó quét và so sánh.",
    rationale:
      "Tách metadata thành lưới key/value để owner nhận diện capability, RAM, VRAM và model active trong vài giây.",
    criteria: [
      "Không có text dính nhau ở 1440 và 360 px.",
      "Card không tạo page overflow.",
      "Model selector chỉ cho phép đúng capability.",
    ],
    after: `
      <div class="mini-banner">CHAT ACTIVE · qwen3:8b</div>
      <h3>Metadata có cấu trúc</h3>
      <div class="mini-grid">
        <div><span>Loại</span><strong>Chat + tool-use</strong></div>
        <div><span>RAM</span><strong>6,1 GB</strong></div>
        <div><span>VRAM</span><strong>4,9 GB</strong></div>
        <div><span>Kích thước</span><strong>4,9 GB</strong></div>
        <div><span>Độ phù hợp</span><strong>Recommended</strong></div>
        <div><span>Quantization</span><strong>Q4_K_M</strong></div>
      </div>
    `,
  },
  knowledge: {
    image: "assets/before/16-knowledge-laptop-1024x768.png",
    alt: "Ảnh audit Knowledge bị cắt nội dung tại 1024x768",
    caption: "BEFORE · KPI và bảng vượt khung, phần bên phải không thể truy cập.",
    rationale:
      "Page luôn vừa viewport; bảng là vùng duy nhất được phép cuộn ngang, còn mobile dùng card mở rộng.",
    criteria: [
      "document.body.scrollWidth bằng innerWidth tại cả bốn viewport.",
      "KPI và CTA không bị cắt.",
      "Mobile card vẫn đọc được MySQL, vector, dung lượng và hành động.",
    ],
    after: `
      <div class="mini-banner">298 NGUỒN · 98 ĐÃ HỌC · 12 ĐANG CHỜ</div>
      <h3>Bảng nằm trong vùng cuộn riêng</h3>
      <div class="mini-row"><strong>Upside — Chat</strong><span>12.840 tin · 10.420 vector</span></div>
      <div class="mini-row"><strong>Phốt Việt Nam</strong><span>9.120 tin · 8.550 vector</span></div>
      <div class="mini-row"><strong>X201 Labs</strong><span>7.430 tin · 6.980 vector</span></div>
    `,
  },
  jobs: {
    image: "assets/before/08-learning-jobs-desktop-1440x900.png",
    alt: "Ảnh audit Learning Jobs hiển thị Completed nhưng tiến độ 0 phần trăm",
    caption: "BEFORE · trạng thái Completed mâu thuẫn trực tiếp với progress 0%.",
    rationale:
      "Status, phase và progress có contract riêng; Completed luôn là 100%, còn unknown không bịa phần trăm.",
    criteria: [
      "Không có Completed dưới 100%.",
      "Failed và Paused giữ mốc tiến độ hợp lệ cuối cùng.",
      "Unknown hiển thị thiếu dữ liệu thay vì 0%.",
    ],
    after: `
      <div class="mini-banner">COMPLETED · 100%</div>
      <h3>Upside — Telegram group sync</h3>
      <div class="mini-grid">
        <div><span>Phase</span><strong>Hoàn tất</strong></div>
        <div><span>Tin đã sync</span><strong>12.840 / 12.840</strong></div>
        <div><span>Vector</span><strong>10.420</strong></div>
      </div>
    `,
  },
  connection: {
    image: "assets/before/24-overview-offline-no-feedback-desktop-1440x900.png",
    alt: "Ảnh audit dashboard đang offline nhưng vẫn báo LIVE",
    caption: "BEFORE · request thất bại nhưng shell vẫn báo LIVE và CONNECTED.",
    rationale:
      "Connection state machine tách API/SSE, ghi thời gian thành công cuối và chỉ báo LIVE sau EventSource open.",
    criteria: [
      "Trong tối đa 5 giây sau mất kết nối không còn nhãn LIVE.",
      "Snapshot cũ có cảnh báo stale và timestamp.",
      "Session expired điều hướng về đăng nhập, không retry vô hạn.",
    ],
    after: `
      <div class="mini-banner">RECONNECTING · DỮ LIỆU CÓ THỂ ĐÃ CŨ</div>
      <h3>Đang kết nối lại SSE</h3>
      <div class="mini-grid">
        <div><span>API</span><strong>Unavailable</strong></div>
        <div><span>SSE</span><strong>Retry 2 / 5</strong></div>
        <div><span>Lần thành công</span><strong>2 phút trước</strong></div>
      </div>
    `,
  },
  modal: {
    image: "assets/before/21-doc-reader-modal-mobile-small-360x800.png",
    alt: "Ảnh audit modal tài liệu bị mất nút đóng tại 360x800",
    caption: "BEFORE · toolbar modal rộng hơn viewport, nút đóng bị đẩy ra ngoài.",
    rationale:
      "Modal giới hạn theo viewport, giữ header/footer trong vùng nhìn thấy và chỉ cuộn phần nội dung.",
    criteria: [
      "Nút Đóng luôn nằm trong viewport và có touch target tối thiểu 44px.",
      "Đóng được bằng nút, backdrop và ESC.",
      "Tab/Shift+Tab bị giữ trong dialog và focus được phục hồi.",
    ],
    after: `
      <div class="mini-modal">
        <div><p class="eyebrow">LOCAL DOCUMENT · READ ONLY</p><h3>USER_GUIDE.md</h3></div>
        <div>Nội dung cuộn nội bộ, chiều rộng luôn nằm trong viewport.</div>
        <div><button class="button button-outline" type="button">ĐÓNG</button></div>
      </div>
    `,
  },
};

const knowledgeSources = [
  {
    id: "-1001928374651",
    title: "Upside — Chat",
    type: "supergroup",
    status: "learned",
    statusLabel: "Đã học",
    mysql: 12840,
    vectors: 10420,
    size: "186 MB",
    learned: "00:03 · 25/07/2026",
    note: "AUTO ON · đồng bộ ổn định",
  },
  {
    id: "-1002056783490",
    title: "Phốt Việt Nam — Thời sự 24/7",
    type: "channel",
    status: "queued",
    statusLabel: "Đang chờ",
    mysql: 9120,
    vectors: 8550,
    size: "142 MB",
    learned: "23:43 · 24/07/2026",
    note: "Chờ worker embedding",
  },
  {
    id: "-1001765432981",
    title: "X201 Labs",
    type: "group",
    status: "failed",
    statusLabel: "Lỗi",
    mysql: 7430,
    vectors: 6980,
    size: "98 MB",
    learned: "22:10 · 24/07/2026",
    note: "Timeout khi đọc media · có thể retry",
  },
  {
    id: "-1001689345720",
    title: "NGHIÊN TRADING CHANNEL",
    type: "channel",
    status: "learned",
    statusLabel: "Đã học",
    mysql: 5530,
    vectors: 5014,
    size: "76 MB",
    learned: "21:54 · 24/07/2026",
    note: "AUTO OFF · học thủ công",
  },
];

const jobStates = {
  queued: {
    badge: "QUEUED",
    tone: "status-warning",
    phase: "Chờ worker",
    progress: 0,
    synced: "0 / 12.840",
    vectors: "0",
    duration: "Chưa bắt đầu",
    error: "Không có",
    action: "pause",
  },
  syncing: {
    badge: "RUNNING",
    tone: "status-live",
    phase: "Đồng bộ Telegram → MySQL",
    progress: 34,
    synced: "8.730 / 12.840",
    vectors: "0",
    duration: "02:14",
    error: "Không có",
    action: "pause",
  },
  embedding: {
    badge: "RUNNING",
    tone: "status-live",
    phase: "Tạo embedding và index",
    progress: 78,
    synced: "12.840 / 12.840",
    vectors: "6.310 / 10.420",
    duration: "05:48",
    error: "Không có",
    action: "pause",
  },
  completed: {
    badge: "COMPLETED",
    tone: "status-live",
    phase: "Hoàn tất",
    progress: 100,
    synced: "12.840 / 12.840",
    vectors: "10.420",
    duration: "08:21",
    error: "Không có",
    action: null,
  },
  failed: {
    badge: "FAILED",
    tone: "status-critical",
    phase: "Embedding bị gián đoạn",
    progress: 72,
    synced: "12.840 / 12.840",
    vectors: "5.480 / 10.420",
    duration: "05:19",
    error: "Vector store timeout lúc 23:46:12",
    action: "retry",
  },
  paused: {
    badge: "PAUSED",
    tone: "status-warning",
    phase: "Đã tạm dừng trong embedding",
    progress: 63,
    synced: "12.840 / 12.840",
    vectors: "2.980 / 10.420",
    duration: "04:02",
    error: "Không có",
    action: "resume",
  },
  unknown: {
    badge: "UNKNOWN",
    tone: "",
    phase: "Backend chưa trả phase",
    progress: null,
    synced: "Không có dữ liệu",
    vectors: "Không có dữ liệu",
    duration: "Không xác định",
    error: "Thiếu progress/processed/total trong response",
    action: "retry",
  },
};

const connectionStates = {
  connecting: {
    title: "Đang thiết lập kết nối",
    label: "CONNECTING",
    labelClass: "connection-reconnecting",
    badge: "CONNECTING",
    badgeClass: "status-warning",
    description:
      "Admin API đang được kiểm tra. UI chưa gắn nhãn LIVE và chưa cho dữ liệu mới ghi đè snapshot.",
    api: "Checking",
    sse: "Chưa mở",
    lastSuccess: "2 phút trước",
    stale: true,
    retry: false,
    login: false,
  },
  live: {
    title: "Đang nhận dữ liệu realtime",
    label: "LIVE",
    labelClass: "connection-live",
    badge: "LIVE",
    badgeClass: "status-live",
    description:
      "SSE đã xác nhận kết nối. Snapshot mới có thể cập nhật các chỉ số vận hành.",
    api: "Connected",
    sse: "Connected",
    lastSuccess: "Vừa xong",
    stale: false,
    retry: false,
    login: false,
  },
  reconnecting: {
    title: "Đang kết nối lại SSE",
    label: "RECONNECTING",
    labelClass: "connection-reconnecting",
    badge: "RECONNECTING",
    badgeClass: "status-warning",
    description:
      "Kết nối realtime đã mất. Snapshot cũ được giữ lại nhưng không còn được xem là dữ liệu live.",
    api: "Degraded",
    sse: "Retry 2 / 5",
    lastSuccess: "2 phút trước",
    stale: true,
    retry: true,
    login: false,
  },
  offline: {
    title: "Dashboard đang offline",
    label: "OFFLINE",
    labelClass: "connection-offline",
    badge: "OFFLINE",
    badgeClass: "status-critical",
    description:
      "Không thể kết nối Admin API. Các số đang thấy là snapshot cũ và thao tác ghi đã tạm khóa.",
    api: "Unavailable",
    sse: "Disconnected",
    lastSuccess: "5 phút trước",
    stale: true,
    retry: true,
    login: false,
  },
  expired: {
    title: "Phiên owner đã hết hạn",
    label: "SESSION EXPIRED",
    labelClass: "connection-expired",
    badge: "EXPIRED",
    badgeClass: "status-critical",
    description:
      "Không tiếp tục retry SSE bằng phiên cũ. Đăng nhập lại để tạo session và CSRF token mới.",
    api: "401 Unauthorized",
    sse: "Stopped",
    lastSuccess: "12 phút trước",
    stale: true,
    retry: false,
    login: true,
  },
};

function setExclusiveActive(nodes, activeNode) {
  nodes.forEach((node) => {
    const active = node === activeNode;
    node.classList.toggle("is-active", active);
    if (node.getAttribute("role") === "tab") node.setAttribute("aria-selected", String(active));
  });
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.hidden = false;
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.hidden = true;
  }, 2600);
}

function navigate(route) {
  const target = $(`[data-screen="${route}"]`);
  if (!target) return;
  $("#toast").hidden = true;
  appState.route = route;
  $$("[data-screen]").forEach((screen) => {
    const active = screen === target;
    screen.hidden = !active;
    screen.classList.toggle("is-active", active);
  });
  setExclusiveActive($$(".primary-nav [data-route]"), $(`.primary-nav [data-route="${route}"]`));
  history.replaceState(null, "", `#${route}`);
  applyRenderState(appState.renderState);
  $("#main-content").focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: "auto" });
}

function updatePriority(priority) {
  const content = priorityContent[priority];
  if (!content) return;
  appState.priority = priority;
  setExclusiveActive($$("[data-priority]"), $(`[data-priority="${priority}"]`));
  $("#beforeImage").src = content.image;
  $("#beforeImage").alt = content.alt;
  $("#beforeCaption").textContent = content.caption;
  $("#priorityRationale").textContent = content.rationale;
  $("#priorityCriteria").innerHTML = content.criteria.map((item) => `<li>${item}</li>`).join("");
  $("#afterPreview").innerHTML = content.after;
  updateComparison(appState.comparison);
}

function updateComparison(mode) {
  appState.comparison = mode;
  setExclusiveActive($$("[data-compare]"), $(`[data-compare="${mode}"]`));
  $("#beforeFigure").hidden = mode !== "before";
  $("#afterPreview").hidden = mode !== "after";
}

function updateViewport(viewport) {
  appState.viewport = viewport;
  document.body.classList.toggle("simulate-mobile", viewport === "mobile");
  setExclusiveActive($$("[data-viewport]"), $(`[data-viewport="${viewport}"]`));
  updateLabStatus();
}

function updateLabStatus() {
  const viewportLabel = appState.viewport === "desktop" ? "Desktop" : "Mobile";
  const renderLabel = {
    content: "Có dữ liệu",
    loading: "Loading",
    error: "Error",
    empty: "Empty",
  }[appState.renderState];
  const networkLabel = appState.network === "online" ? "Online" : "Offline";
  $("#labStatus").textContent = `${viewportLabel} · ${renderLabel} · ${networkLabel}`;
}

function statePanelMarkup(state) {
  if (state === "loading") {
    return `
      <strong>Đang tải dữ liệu…</strong>
      <div class="skeleton-stack" aria-hidden="true">
        <div class="skeleton-line"></div>
        <div class="skeleton-line"></div>
        <div class="skeleton-line"></div>
      </div>
    `;
  }
  if (state === "error") {
    return `
      <strong>Không thể tải dữ liệu</strong>
      <p>Admin API không phản hồi. Dữ liệu cũ không được gắn nhãn LIVE.</p>
      <button type="button" class="button button-primary" data-retry-render>THỬ LẠI</button>
    `;
  }
  return `
    <strong>Chưa có dữ liệu</strong>
    <p>Trạng thái rỗng không giữ lại số liệu mẫu hoặc phần trăm cũ.</p>
    <button type="button" class="button button-outline" data-retry-render>TẢI LẠI</button>
  `;
}

function renderTargetsForRoute(route) {
  const map = {
    models: [$(".model-activation"), $("#modelGrid")],
    knowledge: [$(".kpi-grid"), $(".knowledge-panel"), $("#bulkToolbar")],
    jobs: [$(".job-state-picker"), $("#jobCard"), $(".contract-table")],
    connection: [$(".connection-state-picker"), $("#connectionCard"), $(".state-flow")],
    modal: [$(".modal-demo")],
  };
  return (map[route] || []).filter(Boolean);
}

function applyRenderState(state) {
  appState.renderState = state;
  setExclusiveActive($$("[data-render-state]"), $(`[data-render-state="${state}"]`));
  updateLabStatus();

  const screen = $(`[data-screen="${appState.route}"]`);
  if (!screen || appState.route === "summary") return;

  let panel = $(".screen-state-generated", screen);
  if (!panel) {
    panel = document.createElement("div");
    panel.className = "state-banner screen-state-generated";
    const intro = $(".page-intro", screen);
    intro.insertAdjacentElement("afterend", panel);
  }

  const targets = renderTargetsForRoute(appState.route);
  if (state === "content") {
    panel.hidden = true;
    targets.forEach((target) => {
      if (target.id === "bulkToolbar") {
        target.hidden = appState.selectedSources.size === 0;
      } else {
        target.hidden = false;
      }
    });
    return;
  }

  panel.hidden = false;
  panel.innerHTML = statePanelMarkup(state);
  targets.forEach((target) => {
    target.hidden = true;
  });
}

function setNetwork(network) {
  appState.network = network;
  setExclusiveActive($$("[data-network]"), $(`[data-network="${network}"]`));
  if (network === "offline") {
    setConnectionState("offline");
  } else {
    setConnectionState("live");
    showToast("Đã khôi phục kết nối realtime.");
  }
  updateLabStatus();
}

function showDownload() {
  if (appState.renderState !== "content") applyRenderState("content");
  const card = $("#downloadCard");
  card.hidden = false;
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  if (appState.downloadTimer) return;
  appState.downloadTimer = window.setInterval(() => {
    appState.downloadProgress = Math.min(100, appState.downloadProgress + 1);
    const track = $(".progress-track", card);
    const fill = $(".progress-track > span", card);
    $("#downloadPercent").textContent = `${appState.downloadProgress}%`;
    track.setAttribute("aria-valuenow", String(appState.downloadProgress));
    fill.style.width = `${appState.downloadProgress}%`;
    if (appState.downloadProgress >= 100) {
      window.clearInterval(appState.downloadTimer);
      appState.downloadTimer = null;
      $(".status-chip", card).textContent = "INSTALLED";
      $(".status-chip", card).classList.remove("status-warning");
      $("#cancelDownload").hidden = true;
      showToast("qwen3:14b đã tải xong.");
    }
  }, 900);
}

function cancelDownload() {
  window.clearInterval(appState.downloadTimer);
  appState.downloadTimer = null;
  $("#downloadCard").hidden = true;
  appState.downloadProgress = 47;
  $("#downloadPercent").textContent = "47%";
  const track = $(".progress-track", $("#downloadCard"));
  track.setAttribute("aria-valuenow", "47");
  $(".progress-track > span", $("#downloadCard")).style.width = "47%";
  showToast("Đã hủy tải. Phần model đang tải được dọn khỏi danh sách.");
}

function statusTone(status) {
  if (status === "learned") return "status-live";
  if (status === "queued") return "status-warning";
  return "status-critical";
}

function formatNumber(value) {
  return new Intl.NumberFormat("vi-VN").format(value);
}

function filteredKnowledgeSources() {
  const query = $("#knowledgeSearch").value.trim().toLocaleLowerCase("vi");
  const status = $("#knowledgeFilter").value;
  return knowledgeSources.filter((source) => {
    const matchText =
      !query ||
      `${source.title} ${source.id} ${source.type}`.toLocaleLowerCase("vi").includes(query);
    const matchStatus = status === "all" || source.status === status;
    return matchText && matchStatus;
  });
}

function renderKnowledge() {
  const items = filteredKnowledgeSources();
  const tbody = $("#knowledgeTableBody");
  const cardList = $("#knowledgeCardList");
  const empty = $("#knowledgeEmpty");
  const tableWrap = $("#knowledgeTableWrap");

  tbody.innerHTML = items
    .map(
      (source) => `
        <tr>
          <td>
            <input
              type="checkbox"
              aria-label="Chọn ${source.title}"
              data-select-source="${source.id}"
              ${appState.selectedSources.has(source.id) ? "checked" : ""}
            />
          </td>
          <td><strong>${source.title}</strong><small>${source.id} · ${source.type}</small></td>
          <td><span class="status-chip ${statusTone(source.status)}">${source.statusLabel}</span></td>
          <td><strong>${formatNumber(source.mysql)}</strong><small>tin có quyền truy cập</small></td>
          <td><strong>${formatNumber(source.vectors)}</strong></td>
          <td><strong>${source.size}</strong></td>
          <td>${source.learned}</td>
          <td>${source.note}</td>
          <td>
            <div class="table-actions">
              <button type="button" data-source-note="${source.id}">Ghi chú</button>
              <button type="button" data-source-detail="${source.id}">Chi tiết</button>
            </div>
          </td>
        </tr>
      `,
    )
    .join("");

  cardList.innerHTML = items
    .map(
      (source) => `
        <article class="knowledge-card">
          <header>
            <input
              type="checkbox"
              aria-label="Chọn ${source.title}"
              data-select-source="${source.id}"
              ${appState.selectedSources.has(source.id) ? "checked" : ""}
            />
            <div>
              <h3>${source.title}</h3>
              <small>${source.id} · ${source.type}</small>
            </div>
            <span class="status-chip ${statusTone(source.status)}">${source.statusLabel}</span>
          </header>
          <div class="knowledge-card-summary">
            <div><span>MySQL</span><strong>${formatNumber(source.mysql)}</strong></div>
            <div><span>Vector</span><strong>${formatNumber(source.vectors)}</strong></div>
            <div><span>Dung lượng</span><strong>${source.size}</strong></div>
          </div>
          <div class="knowledge-card-details" id="source-details-${source.id.replace("-", "")}" hidden>
            <dl>
              <div><dt>Lần học</dt><dd>${source.learned}</dd></div>
              <div><dt>Lỗi / ghi chú</dt><dd>${source.note}</dd></div>
            </dl>
            <div class="knowledge-card-actions">
              <button type="button" class="button button-outline" data-source-note="${source.id}">GHI CHÚ</button>
              <button type="button" class="button button-primary" data-collapse-source="${source.id}">THU GỌN</button>
            </div>
          </div>
          <button type="button" class="text-button" data-expand-source="${source.id}">MỞ CHI TIẾT</button>
        </article>
      `,
    )
    .join("");

  const hasItems = items.length > 0;
  empty.hidden = hasItems;
  if (appState.knowledgeView === "table") tableWrap.hidden = !hasItems;
  cardList.hidden = !hasItems;
  updateBulkToolbar();
}

function updateBulkToolbar() {
  const count = appState.selectedSources.size;
  $("#selectionCount").textContent = String(count);
  $("#bulkToolbar").hidden = count === 0 || appState.renderState !== "content";
}

function setKnowledgeView(view) {
  appState.knowledgeView = view;
  const panel = $(".knowledge-panel");
  panel.classList.toggle("force-cards", view === "cards");
  panel.classList.toggle("force-table", view === "table");
  setExclusiveActive(
    $$("[data-knowledge-view]"),
    $(`[data-knowledge-view="${view}"]`),
  );
  renderKnowledge();
}

function resetKnowledgeFilters() {
  $("#knowledgeSearch").value = "";
  $("#knowledgeFilter").value = "all";
  renderKnowledge();
}

function renderJobState(stateKey) {
  const state = jobStates[stateKey];
  if (!state) return;
  appState.jobState = stateKey;
  setExclusiveActive($$("[data-job-state]"), $(`[data-job-state="${stateKey}"]`));

  const badge = $("#jobStatusBadge");
  badge.className = `status-chip ${state.tone}`.trim();
  badge.textContent = state.badge;
  $("#jobPhase").textContent = state.phase;
  $("#jobSynced").textContent = state.synced;
  $("#jobVectors").textContent = state.vectors;
  $("#jobDuration").textContent = state.duration;
  $("#jobError").textContent = state.error;

  const progressArea = $("#jobProgressArea");
  if (state.progress === null) {
    progressArea.innerHTML = `
      <div class="job-no-progress">
        Không có dữ liệu tiến độ đáng tin cậy — không hiển thị phần trăm giả.
      </div>
    `;
  } else {
    progressArea.innerHTML = `
      <div class="job-progress-copy">
        <span>${state.phase}</span>
        <strong>${state.progress}%</strong>
      </div>
      <div
        class="progress-track"
        role="progressbar"
        aria-label="Tiến độ learning job"
        aria-valuemin="0"
        aria-valuemax="100"
        aria-valuenow="${state.progress}"
      >
        <span style="width:${state.progress}%"></span>
      </div>
    `;
  }

  const actions = $("#jobActions");
  const actionLabels = { pause: "TẠM DỪNG", resume: "TIẾP TỤC", retry: "THỬ LẠI" };
  actions.innerHTML = state.action
    ? `<button type="button" class="button button-primary" data-job-action="${state.action}">${actionLabels[state.action]}</button>`
    : `<span class="status-chip status-live">KHÔNG CẦN HÀNH ĐỘNG</span>`;
}

function handleJobAction(action) {
  if (action === "pause") {
    renderJobState("paused");
    showToast("Job đã tạm dừng tại mốc tiến độ gần nhất.");
  } else if (action === "resume") {
    renderJobState("embedding");
    showToast("Job tiếp tục từ phase embedding.");
  } else {
    renderJobState("queued");
    showToast("Đã đưa job về hàng chờ retry.");
  }
}

function setConnectionState(stateKey) {
  const state = connectionStates[stateKey];
  if (!state) return;
  appState.connectionState = stateKey;
  setExclusiveActive(
    $$("[data-connection-state]"),
    $(`[data-connection-state="${stateKey}"]`),
  );

  $("#connectionTitle").textContent = state.title;
  $("#connectionDescription").textContent = state.description;
  $("#apiState").textContent = state.api;
  $("#sseState").textContent = state.sse;
  $("#lastSuccess").textContent = state.lastSuccess;
  $("#staleWarning").hidden = !state.stale;
  $("#retryConnection").hidden = !state.retry;
  $("#loginAgain").hidden = !state.login;

  const label = $("#connectionLabel");
  label.className = `connection-label ${state.labelClass}`;
  label.textContent = state.label;

  const heroBadge = $("#connectionHeroBadge");
  heroBadge.className = `status-chip ${state.badgeClass}`;
  heroBadge.textContent = state.badge;

  const globalLabel = $("#globalConnectionLabel");
  globalLabel.className = `status-chip ${state.badgeClass}`;
  globalLabel.textContent = stateKey === "live" ? "LIVE" : state.badge;

  if (stateKey === "offline") {
    appState.network = "offline";
    setExclusiveActive($$("[data-network]"), $('[data-network="offline"]'));
  } else if (stateKey === "live") {
    appState.network = "online";
    setExclusiveActive($$("[data-network]"), $('[data-network="online"]'));
  }
  updateLabStatus();
}

function retryConnection() {
  setConnectionState("connecting");
  showToast("Đang kiểm tra Admin API và mở lại SSE…");
  window.setTimeout(() => {
    if (appState.network === "offline") {
      setConnectionState("offline");
      showToast("Vẫn chưa có mạng. Snapshot tiếp tục được đánh dấu stale.");
    } else {
      setConnectionState("live");
      showToast("SSE đã kết nối. Trạng thái LIVE được khôi phục.");
    }
  }, 1000);
}

function modalContent(type) {
  if (type === "short") {
    return {
      eyebrow: "ACTIVATION PREVIEW · OWNER CONFIRMATION",
      title: "Xác nhận thay chat model",
      description:
        "Thay qwen3:8b bằng llama3:8b. Embedding model và 75.226 vector hiện tại không bị ảnh hưởng.",
      body: `
        <p id="modalDescription">
          Thay <code>qwen3:8b</code> bằng <code>llama3:8b</code>. Embedding model và
          75.226 vector hiện tại không bị ảnh hưởng.
        </p>
        <h3>Ảnh hưởng</h3>
        <p>Chat mới dùng context 8K và không hỗ trợ tool-use. Các request mới sẽ dùng model mới sau khi owner xác nhận.</p>
      `,
    };
  }
  return {
    eyebrow: "LOCAL DOCUMENT · READ ONLY",
    title: "USER_GUIDE.md",
    description: "Hướng dẫn vận hành Telegram AI Personal Assistant.",
    body: `
      <p id="modalDescription">
        Hướng dẫn vận hành Telegram AI Personal Assistant. Phần nội dung này dài có chủ
        đích để kiểm tra scroll nội bộ và footer luôn truy cập được.
      </p>
      <h3>Mục tiêu</h3>
      <p>
        Ứng dụng biến lịch sử chat được owner cấp quyền trong các group và channel
        Telegram thành một kho tri thức chung. “Học” là đồng bộ vào MySQL, làm sạch,
        tạo embedding và lập chỉ mục để truy xuất; ứng dụng không tự fine-tune model.
      </p>
      <h3>Bắt đầu</h3>
      <p>
        Mở dashboard local, đăng nhập bằng mã owner dùng một lần, sau đó kiểm tra trạng
        thái Admin API và SSE. Chỉ khi header báo LIVE thì snapshot mới được xem là
        realtime.
      </p>
      <h3>Cấp quyền cho nguồn</h3>
      <p>
        Tìm group hoặc channel theo tên, username hay Chat ID. Bật ALLOW trước, rồi chỉ
        bật các quyền cần dùng. Hành động nhạy cảm luôn đi qua preview và owner
        confirmation.
      </p>
      <h3>Đưa nguồn vào bộ não chung</h3>
      <p>
        Chọn nguồn trong Knowledge, kiểm tra số tin MySQL, vector, dung lượng và lần học
        gần nhất. Toolbar bulk xuất hiện khi có lựa chọn và ghi rõ phạm vi tác động.
      </p>
      <h3>Theo dõi Learning Jobs</h3>
      <p>
        Queued bắt đầu ở 0%; syncing dùng vùng 1–50%; embedding dùng 51–99%; completed
        luôn là 100%. Failed và paused giữ tiến độ hợp lệ cuối cùng. Khi backend không
        cung cấp mẫu số tin cậy, giao diện không hiển thị phần trăm.
      </p>
      <h3>An toàn vận hành</h3>
      <p>
        Không render secret đã lưu. Mọi thay đổi provider, model, policy hoặc xóa dữ
        liệu đều phải có impact preview. Khi session hết hạn, quay lại đăng nhập thay vì
        retry SSE với credential cũ.
      </p>
    `,
  };
}

function openModal(type) {
  const content = modalContent(type);
  appState.modalTrigger = document.activeElement;
  const backdrop = $("#prototypeModal");
  const modal = $(".prototype-modal", backdrop);
  $("#modalEyebrow").textContent = content.eyebrow;
  $("#modalTitle").textContent = content.title;
  $("#modalBody").innerHTML = content.body;
  modal.classList.toggle("is-long", type === "long");
  backdrop.hidden = false;
  document.body.style.overflow = "hidden";
  window.requestAnimationFrame(() => $("#closeModal").focus());
}

function closeModal() {
  const backdrop = $("#prototypeModal");
  if (backdrop.hidden) return;
  backdrop.hidden = true;
  document.body.style.overflow = "";
  const trigger = appState.modalTrigger;
  appState.modalTrigger = null;
  if (trigger && typeof trigger.focus === "function") trigger.focus();
}

function trapModalFocus(event) {
  const backdrop = $("#prototypeModal");
  if (backdrop.hidden || event.key !== "Tab") return;
  const focusable = $$(
    'button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    $(".prototype-modal", backdrop),
  ).filter((element) => !element.hidden && element.offsetParent !== null);
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

document.addEventListener("click", (event) => {
  const routeButton = event.target.closest("[data-route]");
  if (routeButton) {
    event.preventDefault();
    navigate(routeButton.dataset.route);
    return;
  }

  const priorityButton = event.target.closest("[data-priority]");
  if (priorityButton) {
    updatePriority(priorityButton.dataset.priority);
    return;
  }

  const compareButton = event.target.closest("[data-compare]");
  if (compareButton) {
    updateComparison(compareButton.dataset.compare);
    return;
  }

  const viewportButton = event.target.closest("[data-viewport]");
  if (viewportButton) {
    updateViewport(viewportButton.dataset.viewport);
    return;
  }

  const renderButton = event.target.closest("[data-render-state]");
  if (renderButton) {
    applyRenderState(renderButton.dataset.renderState);
    return;
  }

  const retryRender = event.target.closest("[data-retry-render]");
  if (retryRender) {
    applyRenderState("loading");
    window.setTimeout(() => applyRenderState("content"), 700);
    return;
  }

  const networkButton = event.target.closest("[data-network]");
  if (networkButton) {
    setNetwork(networkButton.dataset.network);
    return;
  }

  const modelDetails = event.target.closest("[data-model-details]");
  if (modelDetails) {
    const detail = $(`#details-${modelDetails.dataset.modelDetails}`);
    const open = detail.hidden;
    detail.hidden = !open;
    modelDetails.textContent = open ? "Thu gọn" : "Xem chi tiết";
    return;
  }

  const knowledgeView = event.target.closest("[data-knowledge-view]");
  if (knowledgeView) {
    setKnowledgeView(knowledgeView.dataset.knowledgeView);
    return;
  }

  const sourceCheckbox = event.target.closest("[data-select-source]");
  if (sourceCheckbox) {
    const id = sourceCheckbox.dataset.selectSource;
    if (sourceCheckbox.checked) appState.selectedSources.add(id);
    else appState.selectedSources.delete(id);
    renderKnowledge();
    return;
  }

  const expandSource = event.target.closest("[data-expand-source]");
  if (expandSource) {
    const id = expandSource.dataset.expandSource.replace("-", "");
    $(`#source-details-${id}`).hidden = false;
    expandSource.hidden = true;
    return;
  }

  const collapseSource = event.target.closest("[data-collapse-source]");
  if (collapseSource) {
    const id = collapseSource.dataset.collapseSource.replace("-", "");
    $(`#source-details-${id}`).hidden = true;
    $(`[data-expand-source="${collapseSource.dataset.collapseSource}"]`).hidden = false;
    return;
  }

  const sourceNote = event.target.closest("[data-source-note]");
  if (sourceNote) {
    showToast("Prototype: mở form ghi chú cho nguồn đã chọn.");
    return;
  }

  const sourceDetail = event.target.closest("[data-source-detail]");
  if (sourceDetail) {
    setKnowledgeView("cards");
    const id = sourceDetail.dataset.sourceDetail.replace("-", "");
    const detail = $(`#source-details-${id}`);
    if (detail) {
      detail.hidden = false;
      detail.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    return;
  }

  if (event.target.closest("[data-reset-knowledge]")) {
    resetKnowledgeFilters();
    return;
  }

  const jobState = event.target.closest("[data-job-state]");
  if (jobState) {
    renderJobState(jobState.dataset.jobState);
    return;
  }

  const jobAction = event.target.closest("[data-job-action]");
  if (jobAction) {
    handleJobAction(jobAction.dataset.jobAction);
    return;
  }

  const connectionState = event.target.closest("[data-connection-state]");
  if (connectionState) {
    setConnectionState(connectionState.dataset.connectionState);
    return;
  }

  const modalButton = event.target.closest("[data-open-modal]");
  if (modalButton) {
    openModal(modalButton.dataset.openModal);
    return;
  }

  if (event.target === $("#prototypeModal")) closeModal();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#prototypeModal").hidden) {
    event.preventDefault();
    closeModal();
    return;
  }
  trapModalFocus(event);
});

$("#openPriorityScreen").addEventListener("click", () => navigate(appState.priority));
$("#showDownloadState").addEventListener("click", showDownload);
$("#downloadModel").addEventListener("click", showDownload);
$("#cancelDownload").addEventListener("click", cancelDownload);
$("#previewActivation").addEventListener("click", () => openModal("short"));
$("#chatModelSelect").addEventListener("change", () => {
  $("#activationHint").textContent =
    "Preview: đổi chat model không re-index, nhưng capability tool-use và context có thể thay đổi.";
});
$("#embeddingModelSelect").addEventListener("change", () => {
  $("#activationHint").textContent =
    "Preview bắt buộc: 298 nguồn · 75.226 vector sẽ cần kiểm tra dimension và kế hoạch re-index.";
});
$("#knowledgeSearch").addEventListener("input", renderKnowledge);
$("#knowledgeFilter").addEventListener("change", renderKnowledge);
$("#clearKnowledgeFilter").addEventListener("click", resetKnowledgeFilters);
$("#clearSelection").addEventListener("click", () => {
  appState.selectedSources.clear();
  renderKnowledge();
});
$("#retryConnection").addEventListener("click", retryConnection);
$("#loginAgain").addEventListener("click", () => {
  showToast("Prototype: chuyển về màn đăng nhập owner.");
});
$("#closeModal").addEventListener("click", closeModal);
$("#cancelModal").addEventListener("click", closeModal);
$("#confirmModal").addEventListener("click", () => {
  closeModal();
  showToast("Đã xác nhận trong prototype. Không có dữ liệu production bị thay đổi.");
});

function init() {
  updatePriority("models");
  updateComparison("before");
  renderKnowledge();
  renderJobState("queued");
  setConnectionState("live");
  updateViewport("desktop");
  applyRenderState("content");
  const hashRoute = location.hash.replace("#", "");
  navigate($(`[data-screen="${hashRoute}"]`) ? hashRoute : "summary");
}

init();
