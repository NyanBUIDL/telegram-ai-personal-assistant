const API_ROOT = "/api/v1";

export class ApiError extends Error {
  constructor(message, status, payload = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

let csrfToken = "";
let authFailureHandler = null;

export function setCsrfToken(value) {
  csrfToken = value || "";
}

export function onAuthFailure(handler) {
  authFailureHandler = handler;
  return () => {
    if (authFailureHandler === handler) authFailureHandler = null;
  };
}

async function request(path, options = {}) {
  const { responseType, ...fetchOptions } = options;
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  const isWrite = !["GET", "HEAD", "OPTIONS"].includes(method);
  if (options.body !== undefined && !(options.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (isWrite && csrfToken && path !== "/auth/login") {
    headers.set("X-CSRF-Token", csrfToken);
  }

  let response;
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      ...fetchOptions,
      method,
      headers,
      credentials: "same-origin",
      body:
        options.body === undefined || options.body instanceof FormData
          ? options.body
          : JSON.stringify(options.body),
    });
  } catch (error) {
    throw new ApiError(
      "Không kết nối được Admin API. Hãy kiểm tra ứng dụng đang chạy.",
      0,
      error,
    );
  }

  const contentType = response.headers.get("content-type") || "";
  const payload =
    responseType === "blob" && response.ok
      ? await response.blob()
      : contentType.includes("application/json")
        ? await response.json()
        : await response.text();

  if (!response.ok) {
    const message =
      typeof payload === "object" && payload?.detail
        ? String(payload.detail)
        : `Admin API trả về HTTP ${response.status}.`;
    const error = new ApiError(message, response.status, payload);
    if (response.status === 401 && authFailureHandler) authFailureHandler(error);
    throw error;
  }
  return payload;
}

export const api = {
  session: () => request("/auth/session"),
  login: (code) => request("/auth/login", { method: "POST", body: { code } }),
  logout: () => request("/auth/logout", { method: "POST" }),
  overview: () => request("/overview"),
  groups: ({
    category = "all",
    query = "",
    chatType = "",
    policyStatus = "",
    learningStatus = "",
    activityState = "",
    inactiveDays = 60,
    sortBy = "last_seen",
    sortDir = "desc",
    page = 1,
    pageSize = 10,
  } = {}) => {
    const params = new URLSearchParams({
      category,
      query,
      inactive_days: String(inactiveDays),
      sort_by: sortBy,
      sort_dir: sortDir,
      page: String(page),
      page_size: String(pageSize),
    });
    if (chatType) params.set("chat_type", chatType);
    if (policyStatus) params.set("policy_status", policyStatus);
    if (learningStatus) params.set("learning_status", learningStatus);
    if (activityState) params.set("activity_state", activityState);
    return request(`/groups?${params}`);
  },
  preferences: () => request("/preferences"),
  updatePreferences: (body) => request("/preferences", { method: "PUT", body }),
  groupRecommendations: (inactiveDays = 60) =>
    request(`/groups/recommendations?inactive_days=${inactiveDays}`),
  group: (chatId) => request(`/groups/${encodeURIComponent(chatId)}`),
  checkCoverage: (chatId) =>
    request(`/groups/${encodeURIComponent(chatId)}/coverage-check`, { method: "POST" }),
  previewRecovery: (chatId) =>
    request(`/groups/${encodeURIComponent(chatId)}/recovery-preview`, { method: "POST" }),
  createGroupAction: (chatId, body) =>
    request(`/groups/${encodeURIComponent(chatId)}/actions`, {
      method: "POST",
      body,
    }),
  exportGroupHistory: (
    chatId,
    { searchTerms = "", promotionOnly = false, onlyMatches = false } = {},
  ) => {
    const params = new URLSearchParams();
    if (searchTerms.trim()) params.set("search_terms", searchTerms.trim());
    if (promotionOnly) params.set("promotion_only", "true");
    if (onlyMatches) params.set("only_matches", "true");
    const suffix = params.size ? `?${params}` : "";
    return request(
      `/groups/${encodeURIComponent(chatId)}/history-export${suffix}`,
      { responseType: "blob" },
    );
  },
  historyLinkDeleteCandidates: (chatId, { mode, page = 1, pageSize = 50 }) =>
    request(
      `/groups/${encodeURIComponent(chatId)}/history-delete-candidates?${new URLSearchParams({
        mode,
        page: String(page),
        page_size: String(pageSize),
      })}`,
    ),
  previewHistoryLinkDeletion: (chatId, body) =>
    request(`/groups/${encodeURIComponent(chatId)}/history-delete-preview`, {
      method: "POST",
      body,
    }),
  filterHistoryDeleteCandidatesWithAi: (chatId, body) =>
    request(`/groups/${encodeURIComponent(chatId)}/history-ai-delete-filter`, {
      method: "POST",
      body,
    }),
  setPermission: (chatId, permission, enabled) =>
    request(`/groups/${encodeURIComponent(chatId)}/permissions`, {
      method: "POST",
      body: { permission, enabled },
    }),
  setAiRoute: (chatId, body) =>
    request(`/groups/${encodeURIComponent(chatId)}/ai-route`, {
      method: "PUT",
      body,
    }),
  setAiEfficiency: (chatId, body) =>
    request(`/groups/${encodeURIComponent(chatId)}/ai-efficiency`, {
      method: "PUT",
      body,
    }),
  setLimits: (chatId, body) =>
    request(`/groups/${encodeURIComponent(chatId)}/limits`, {
      method: "PUT",
      body,
    }),
  previewLeaveGroup: (chatId, body) =>
    request(`/groups/${encodeURIComponent(chatId)}/leave-preview`, {
      method: "POST",
      body,
    }),
  pendingActions: (state = "pending", limit = 100) =>
    request(`/pending-actions?state=${encodeURIComponent(state)}&limit=${limit}`),
  confirmAction: (actionId) =>
    request(`/pending-actions/${encodeURIComponent(actionId)}/confirm`, {
      method: "POST",
    }),
  cancelAction: (actionId) =>
    request(`/pending-actions/${encodeURIComponent(actionId)}/cancel`, {
      method: "POST",
    }),
  knowledgeSources: ({
    status = "",
    query = "",
    chatType = "",
    autoKnowledge = "",
    sortBy = "title",
    sortDir = "asc",
    page = 1,
    pageSize = 50,
  } = {}) => {
    const params = new URLSearchParams({
      source_status: status,
      query,
      sort_by: sortBy,
      sort_dir: sortDir,
      page: String(page),
      page_size: String(pageSize),
    });
    if (chatType) params.set("chat_type", chatType);
    if (autoKnowledge !== "") params.set("auto_knowledge", String(autoKnowledge));
    return request(`/knowledge/sources?${params}`);
  },
  exportKnowledgeSources: () =>
    request("/knowledge/sources/export", { responseType: "blob" }),
  createEnableAllKnowledgeAction: () =>
    request("/knowledge/enable-all-action", { method: "POST" }),
  createEnableSelectionAction: (chatIds, limit = 1000) =>
    request("/knowledge/enable-selection-action", {
      method: "POST",
      body: { chat_ids: chatIds.map(Number), limit },
    }),
  setKnowledgeNote: (chatId, note) =>
    request(`/knowledge/sources/${encodeURIComponent(chatId)}/note`, {
      method: "PUT",
      body: { note },
    }),
  previewDeleteKnowledge: (chatId, scope, confirmation = null) =>
    request(`/knowledge/sources/${encodeURIComponent(chatId)}/delete-preview`, {
      method: "POST",
      body: { scope, confirmation },
    }),
  learningJobs: ({ status = "", limit = 200 } = {}) =>
    request(
      `/learning-jobs?job_status=${encodeURIComponent(status)}&limit=${limit}`,
    ),
  historyBackfillJobs: (chatId, limit = 10) =>
    request(
      `/history-backfill-jobs?chat_id=${encodeURIComponent(chatId)}&limit=${limit}`,
    ),
  historyLinkDeleteJobs: (chatId, limit = 10) =>
    request(
      `/history-link-delete-jobs?chat_id=${encodeURIComponent(chatId)}&limit=${limit}`,
    ),
  pauseAllLearning: () => request("/learning-jobs/pause-all", { method: "POST" }),
  resumeAllLearning: () =>
    request("/learning-jobs/resume-all", { method: "POST" }),
  changeLearningJob: (jobId, operation) =>
    request(
      `/learning-jobs/${encodeURIComponent(jobId)}/${encodeURIComponent(operation)}`,
      { method: "POST" },
    ),
  aiConfig: () => request("/ai/config"),
  setProvider: (provider) =>
    request("/ai/provider", { method: "PUT", body: { provider } }),
  ollamaModels: () => request("/ollama/models"),
  pullOllamaModel: (model) =>
    request("/ollama/models/pull", { method: "POST", body: { model } }),
  ollamaDownloads: (limit = 20) =>
    request(`/ollama/downloads?limit=${encodeURIComponent(limit)}`),
  cancelOllamaDownload: (jobId) =>
    request(`/ollama/downloads/${encodeURIComponent(jobId)}/cancel`, {
      method: "POST",
    }),
  previewActivateOllama: (chatModel, embeddingModel) =>
    request("/ollama/activate-preview", {
      method: "POST",
      body: { chat_model: chatModel, embedding_model: embeddingModel },
    }),
  activateOllama: (chatModel, embeddingModel) =>
    request("/ollama/activate", {
      method: "PUT",
      body: { chat_model: chatModel, embedding_model: embeddingModel },
    }),
  previewDeleteOllamaModel: (model) =>
    request("/ollama/models/delete-preview", {
      method: "POST",
      body: { model },
    }),
  storage: () => request("/storage"),
  cleanupPreview: () => request("/storage/cleanup-preview", { method: "POST" }),
  createCleanupAction: () =>
    request("/storage/cleanup-action", { method: "POST" }),
  workers: () => request("/workers"),
  audit: ({ query = "", outcome = "", page = 1, pageSize = 100 } = {}) =>
    request(
      `/audit?query=${encodeURIComponent(query)}&outcome=${encodeURIComponent(
        outcome,
      )}&page=${page}&page_size=${pageSize}`,
    ),
  documents: () => request("/docs"),
  document: (id) => request(`/docs/${encodeURIComponent(id)}`),
};

export function connectEvents({ onOpen, onSnapshot, onError }) {
  const events = new EventSource(`${API_ROOT}/events`, { withCredentials: true });
  events.onopen = () => onOpen?.();
  events.addEventListener("dashboard-snapshot", (event) => {
    try {
      onSnapshot(JSON.parse(event.data));
    } catch (error) {
      onError?.(error);
    }
  });
  events.onerror = (error) => onError?.(error);
  return () => events.close();
}
