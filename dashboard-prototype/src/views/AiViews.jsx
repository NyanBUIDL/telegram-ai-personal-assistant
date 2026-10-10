import { useEffect, useMemo, useRef, useState } from "react";
import {
  Brain,
  CheckCircle,
  Cloud,
  Cpu,
  DownloadSimple,
  LockKey,
  Robot,
  Sparkle,
  Trash,
  Warning,
  X,
} from "@phosphor-icons/react";

import { api } from "../api.js";
import { formatBytes, formatDate, formatNumber, humanize } from "../format.js";
import { useResource } from "../hooks.js";
import { useDialogA11y } from "../useDialogA11y.js";
import {
  Badge,
  EmptyState,
  ErrorState,
  LoadingState,
  PanelHeader,
} from "../ui.jsx";

export function AiView({ refreshKey, onToast, onNavigate }) {
  const resource = useResource(api.aiConfig, [], refreshKey);
  const [selectedProvider, setSelectedProvider] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);
  const [providerError, setProviderError] = useState(null);
  const [acceptedProvider, setAcceptedProvider] = useState(null);

  useEffect(() => {
    if (resource.data) { setSelectedProvider(resource.data.provider); setAcceptedProvider(null); }
  }, [resource.data]);

  const changeProvider = async () => {
    setSaving(true);
    setProviderError(null);
    try {
      const result = await api.setProvider(selectedProvider);
      setAcceptedProvider(result.provider);
      setSelectedProvider(result.provider);
      onToast(`Máy chủ đã chọn provider ${result.provider}.`);
      setConfirming(false);
      await resource.reload();
    } catch (error) {
      setSelectedProvider(acceptedProvider || resource.data.provider);
      setConfirming(false);
      setProviderError(error);
      onToast(error.message, "error");
    } finally {
      setSaving(false);
    }
  };

  if (resource.loading && !resource.data) return <LoadingState label="Đang tải cấu hình AI…" />;
  if (resource.error && !resource.data)
    return <ErrorState error={resource.error} onRetry={resource.reload} />;
  if (!resource.data) return null;
  const config = { ...resource.data, provider: acceptedProvider || resource.data.provider };

  const usage = config.usage_24h || {};
  const efficiency = config.efficiency || {};
  const totalTokens = (usage.input_tokens || 0) + (usage.output_tokens || 0);
  const operationLabels = {
    answer: "Trả lời AI",
    embedding: "Tạo embedding",
    digest: "Tổng hợp bản tin",
    summarize: "Tóm tắt",
  };
  const featureRows = Object.entries(usage.features || {}).sort(
    ([, left], [, right]) =>
      (right.input_tokens || 0) + (right.output_tokens || 0) -
      ((left.input_tokens || 0) + (left.output_tokens || 0)),
  );
  const providerRows = Object.entries(usage.provider_models || {}).sort(
    ([, left], [, right]) => (right.tokens || 0) - (left.tokens || 0),
  );
  const sourceRows = Object.entries(usage.groups || {}).sort(
    ([, left], [, right]) => (right.tokens || 0) - (left.tokens || 0),
  );

  return (
    <section className="ops-stack">
      {providerError ? <div role="alert"><p>{providerError.message}</p><p>Đang hiển thị provider được xác nhận gần nhất. Mở Kết nối rồi dùng cấu hình AI trên Windows; không tự bật cloud fallback.</p><button className="button button--outline" onClick={() => onNavigate?.("connections")}>Mở Kết nối</button></div> : null}
      <section className="ops-mode-switch">
        <div>
          <p className="eyebrow">GLOBAL PROVIDER · LIVE</p>
          <h2>Nhà cung cấp AI</h2>
          <span>
            Provider toàn cục; từng group vẫn có AI mode và fallback riêng.
          </span>
        </div>
        <div className="segment-control" role="radiogroup" aria-label="Nhà cung cấp AI">
          {config.providers.map((provider) => (
            <button
              key={provider}
              role="radio"
              disabled={saving}
              aria-checked={selectedProvider === provider}
              className={selectedProvider === provider ? "is-active" : ""}
              onClick={() => {
                setSelectedProvider(provider);
                setConfirming(provider !== config.provider);
              }}
            >
              {provider.toUpperCase()}
            </button>
          ))}
        </div>
      </section>

      {confirming ? (
        <section className="integration-readiness integration-readiness--live">
          <div>
            <p className="eyebrow">CONFIGURATION REVIEW</p>
            <h2>Chuyển provider sang {selectedProvider.toUpperCase()}?</h2>
            <span>
              Backend sẽ kiểm tra credential hoặc Ollama local trước khi áp dụng. Secret không
              được gửi tới trình duyệt.
            </span>
          </div>
          <div className="ops-panel-actions">
            <button
              className="button button--outline"
              disabled={saving}
              onClick={() => {
                setSelectedProvider(config.provider);
                setConfirming(false);
              }}
            >
              Hủy
            </button>
            <button
              className="button button--primary"
              disabled={saving}
              onClick={changeProvider}
            >
              <CheckCircle size={18} />
              {saving ? "Đang áp dụng…" : "Xác nhận chuyển"}
            </button>
          </div>
        </section>
      ) : null}

      <section className="ops-resource-strip">
        <div>
          <Cloud size={30} />
          <span>Provider</span>
          <b>{(acceptedProvider || config.provider).toUpperCase()}</b>
          <Badge tone={config.provider === "off" ? "magenta" : "success"}>
            {config.provider === "off" ? "OFF" : "ACTIVE"}
          </Badge>
        </div>
        <div>
          <Robot size={30} />
          <span>Chat model</span>
          <b>{config.model || "—"}</b>
          <Badge tone="teal">CHAT</Badge>
        </div>
        <div>
          <Brain size={30} />
          <span>Embedding</span>
          <b>{config.embedding_model || "—"}</b>
          <Badge tone={config.embeddings_enabled ? "success" : "yellow"}>
            {config.embeddings_enabled ? "ON" : "OFF"}
          </Badge>
        </div>
        <div>
          <Sparkle size={30} />
          <span>Request 24 giờ</span>
          <b>{formatNumber(usage.requests)}</b>
          <Badge tone={usage.failed ? "yellow" : "success"}>
            {usage.failed || 0} FAILED
          </Badge>
        </div>
      </section>

      <section className="ops-two-column">
        <section className="panel">
          <PanelHeader
            eyebrow="AI USAGE · 24 HOURS"
            title="Sử dụng và chi phí"
            action={<Badge tone="teal">{formatNumber(totalTokens)} TOKENS</Badge>}
          />
          <div className="knowledge-status-grid">
            <div>
              <span>Input tokens</span>
              <b>{formatNumber(usage.input_tokens)}</b>
              <small>24H</small>
            </div>
            <div>
              <span>Output tokens</span>
              <b>{formatNumber(usage.output_tokens)}</b>
              <small>24H</small>
            </div>
            <div>
              <span>Chi phí ước tính</span>
              <b>${Number(usage.estimated_cost_usd || 0).toFixed(4)}</b>
              <small>USD</small>
            </div>
            <div>
              <span>Request lỗi</span>
              <b>{formatNumber(usage.failed)}</b>
              <small>24H</small>
            </div>
          </div>
          <div className="ops-operation-list">
            <b>Công năng tốn token nhất</b>
            {featureRows.length ? (
              featureRows.map(([feature, values]) => (
                <div key={feature}>
                  <span>{operationLabels[feature] || feature}</span>
                  <strong>{formatNumber(values.requests)} request</strong>
                  <small>
                    {formatNumber(
                      (values.input_tokens || 0) + (values.output_tokens || 0),
                    )}{" "}
                    tokens · {formatNumber(values.failed)} lỗi
                  </small>
                </div>
              ))
            ) : (
              <span>Chưa có lần gọi AI nào trong 24 giờ gần nhất.</span>
            )}
          </div>
        </section>

        <section className="panel">
          <PanelHeader eyebrow="BUDGET GUARD" title="Ngân sách AI" />
          <div className="ops-setting-list">
            <div className="ops-setting-row">
              <div>
                <b>Ngân sách ngày</b>
                <span>Giới hạn cấu hình tại backend</span>
              </div>
              <Badge tone="paper">${Number(config.daily_budget_usd).toFixed(2)}</Badge>
            </div>
            <div className="ops-setting-row">
              <div>
                <b>Ngân sách tháng</b>
                <span>Giới hạn cấu hình tại backend</span>
              </div>
              <Badge tone="paper">${Number(config.monthly_budget_usd).toFixed(2)}</Badge>
            </div>
            <div className="ops-setting-row">
              <div>
                <b>Local / Cloud</b>
                <span>{formatNumber(usage.local_requests)} local · {formatNumber(usage.cloud_requests)} cloud trong 24 giờ</span>
              </div>
              <Badge tone="teal">{Math.round((usage.cache_hit_rate || 0) * 100)}% CACHE</Badge>
            </div>
            <div className="ops-setting-row">
              <div>
                <b>Embedding mặc định</b>
                <span>{config.embedding_model} · {efficiency.embedding_version || "local-v1"}</span>
              </div>
              <Badge tone="success">LOCAL</Badge>
            </div>
            <div className="ops-setting-row">
              <div>
                <b>Preset mặc định</b>
                <span>RAG top_k {efficiency.rag_top_k || "—"} · tối đa {formatNumber(efficiency.rag_max_context_tokens)} token</span>
              </div>
              <Badge tone="paper">{String(efficiency.default_preset || "balanced").toUpperCase()}</Badge>
            </div>
            <div className="ops-local-warning">
              <LockKey size={22} weight="fill" />
              <b>Secret boundary</b>
              <span>Dashboard chỉ biết trạng thái provider, không đọc API key.</span>
            </div>
          </div>
        </section>
      </section>

      <section className="ops-two-column">
        <section className="panel">
          <PanelHeader eyebrow="TOKEN TELEMETRY · 24 HOURS" title="Provider / model" />
          <div className="ops-operation-list">
            {providerRows.length ? providerRows.map(([name, values]) => (
              <div key={name}>
                <span>{name}</span>
                <strong>{formatNumber(values.tokens)} tokens</strong>
                <small>
                  {formatNumber(values.requests)} request · ${Number(values.estimated_cost_usd || 0).toFixed(4)} · {String(values.execution || "legacy").toUpperCase()}
                </small>
              </div>
            )) : <span>Chưa có telemetry provider/model trong 24 giờ gần nhất.</span>}
          </div>
        </section>
        <section className="panel">
          <PanelHeader eyebrow="TOKEN TELEMETRY · 24 HOURS" title="Nguồn tốn token" />
          <div className="ops-operation-list">
            {sourceRows.length ? sourceRows.slice(0, 10).map(([chatId, values]) => (
              <div key={chatId}>
                <span>Chat {chatId}</span>
                <strong>{formatNumber(values.tokens)} tokens</strong>
                <small>{formatNumber(values.requests)} request · ${Number(values.estimated_cost_usd || 0).toFixed(4)}</small>
              </div>
            )) : <span>Chưa có request AI gắn với nguồn cụ thể trong 24 giờ gần nhất.</span>}
          </div>
        </section>
      </section>
    </section>
  );
}

export function ModelsView({ refreshKey, onCreatedAction, onToast }) {
  const resource = useResource(api.ollamaModels, [], refreshKey);
  const downloads = useResource(() => api.ollamaDownloads(20), [], refreshKey);
  const [filter, setFilter] = useState("all");
  const [pullModel, setPullModel] = useState("");
  const [chatModel, setChatModel] = useState("");
  const [embeddingModel, setEmbeddingModel] = useState("");
  const [busy, setBusy] = useState("");
  const [activationPreview, setActivationPreview] = useState(null);
  const [activationError, setActivationError] = useState(null);
  const [activationUncertain, setActivationUncertain] = useState(false);
  const activationTriggerRef = useRef(null);
  const activationRecoveryRef = useRef(null);
  const wasActivationUncertain = useRef(false);
  const activationDialogRef = useDialogA11y(
    Boolean(activationPreview),
    () => { if (!busy) setActivationPreview(null); },
    activationTriggerRef,
  );
  useEffect(() => {
    if (!activationPreview && (activationUncertain || wasActivationUncertain.current)) {
      (activationUncertain ? activationRecoveryRef.current : activationTriggerRef.current)?.focus();
    }
    wasActivationUncertain.current = activationUncertain;
  }, [activationPreview, activationUncertain]);

  const allModels = useMemo(() => resource.data?.items || [], [resource.data]);
  const chatModels = useMemo(
    () => allModels.filter((model) => model.capabilities?.includes("chat")),
    [allModels],
  );
  const embeddingModels = useMemo(
    () => allModels.filter((model) => model.capabilities?.includes("embedding")),
    [allModels],
  );
  const activeDownload = (downloads.data?.items || []).find((job) =>
    ["queued", "running", "cancel_requested"].includes(job.status),
  );
  const models = allModels.filter((model) => {
    if (filter === "active") return model.is_chat_model || model.is_embedding_model;
    if (filter === "fit") return model.fit === "recommended";
    return true;
  });
  useEffect(() => {
    if (!allModels.length) return;
    const currentChat =
      chatModels.find((model) => model.is_chat_model)?.name || chatModels[0]?.name || "";
    const currentEmbedding =
      embeddingModels.find((model) => model.is_embedding_model)?.name ||
      embeddingModels[0]?.name ||
      "";
    setChatModel((value) =>
      chatModels.some((model) => model.name === value) ? value : currentChat,
    );
    setEmbeddingModel((value) =>
      embeddingModels.some((model) => model.name === value)
        ? value
        : currentEmbedding,
    );
  }, [allModels, chatModels, embeddingModels]);

  useEffect(() => {
    if (!activeDownload) return undefined;
    const timer = window.setInterval(() => {
      downloads.reload();
      resource.reload();
    }, 1800);
    return () => window.clearInterval(timer);
  }, [activeDownload?.id, activeDownload?.status, downloads.reload, resource.reload]);

  const run = async (key, work, success) => {
    setBusy(key);
    try {
      const result = await work();
      onToast(success);
      await resource.reload();
      return result;
    } catch (error) {
      onToast(error.message, "error");
      return null;
    } finally {
      setBusy("");
    }
  };

  const pull = async (event) => {
    event.preventDefault();
    setBusy("pull");
    try {
      await api.pullOllamaModel(pullModel);
      onToast(`Đã đưa ${pullModel} vào hàng đợi tải.`);
      setPullModel("");
      await downloads.reload();
    } catch (error) {
      onToast(error.message, "error");
    } finally {
      setBusy("");
    }
  };

  const previewActivation = async () => {
    if (activationUncertain || busy) return;
    setBusy("activation-preview");
    setActivationError(null);
    try {
      setActivationPreview(await api.previewActivateOllama(chatModel, embeddingModel));
    } catch (error) {
      onToast(error.message, "error");
    } finally {
      setBusy("");
    }
  };

  const activate = async () => {
    if (activationUncertain || busy) return;
    setBusy("activate");
    setActivationError(null);
    try {
      await api.activateOllama(activationPreview.requested.chat_model, activationPreview.requested.embedding_model);
      onToast("Máy chủ đã xác nhận kích hoạt model.");
      setBusy("");
      setActivationPreview(null);
      await resource.reload();
    } catch (error) {
      if (error.status === 0 || error.status >= 500) setActivationUncertain(true);
      setActivationError(error);
    } finally {
      setBusy("");
    }
  };

  const reconcileActivation = async () => {
    setBusy("activation-reconcile");
    try {
      const models = await resource.reload();
      if (!models) throw new Error("Chưa đọc được trạng thái model mới. Giữ kết quả chưa rõ và đối chiếu lại.");
      await api.aiConfig();
      setActivationUncertain(false);
      setActivationError(null);
      setBusy("");
      setActivationPreview(null);
      onToast("Đã đọc lại cấu hình và model. Hãy xem ảnh hưởng mới trước khi kích hoạt.");
    } catch (error) {
      setActivationError(error);
    } finally {
      setBusy("");
    }
  };

  const cancelDownload = async (job) => {
    setBusy(`cancel-${job.id}`);
    try {
      await api.cancelOllamaDownload(job.id);
      onToast(`Đã gửi yêu cầu hủy tải ${job.payload?.model || "model"}.`);
      await downloads.reload();
    } catch (error) {
      onToast(error.message, "error");
    } finally {
      setBusy("");
    }
  };

  const previewDelete = async (model) => {
    const action = await run(
      `delete-${model}`,
      () => api.previewDeleteOllamaModel(model),
      "Đã tạo preview xóa model; cần owner xác nhận.",
    );
    if (action) onCreatedAction(action);
  };

  return (
    <section className="ops-stack">
      <section className="ops-mode-switch">
        <div>
          <p className="eyebrow">OLLAMA LOCAL · LIVE</p>
          <h2>Local Model Manager</h2>
          <span>Danh sách và thao tác được đọc trực tiếp từ Ollama trên máy này.</span>
        </div>
        <form className="model-pull-form" onSubmit={pull}>
          <input
            value={pullModel}
            onChange={(event) => setPullModel(event.target.value)}
            placeholder="Ví dụ: qwen3:8b"
            aria-label="Tên model cần tải"
            required
          />
          <button className="button button--primary" disabled={Boolean(busy)}>
            <DownloadSimple size={18} />
            Tải model
          </button>
        </form>
      </section>

      {activeDownload ? (
        <section className="model-download" aria-live="polite">
          <div>
            <p className="eyebrow">OLLAMA DOWNLOAD · {humanize(activeDownload.status)}</p>
            <h3>{activeDownload.payload?.model || "Model Ollama"}</h3>
            <span>
              {activeDownload.payload?.status || humanize(activeDownload.phase)}
              {activeDownload.payload?.completed_bytes && activeDownload.payload?.total_bytes
                ? ` · ${formatBytes(activeDownload.payload.completed_bytes)} / ${formatBytes(
                    activeDownload.payload.total_bytes,
                  )}`
                : ""}
            </span>
          </div>
          <div className="model-download-progress">
            {activeDownload.progress != null ? (
              <>
                <b>{activeDownload.progress}%</b>
                <span
                  className="job-progress"
                  role="progressbar"
                  aria-valuemin="0"
                  aria-valuemax="100"
                  aria-valuenow={activeDownload.progress}
                >
                  <i style={{ width: `${activeDownload.progress}%` }} />
                </span>
              </>
            ) : (
              <b>Đang tải…</b>
            )}
          </div>
          <button
            className="button button--danger"
            disabled={Boolean(busy) || activeDownload.status === "cancel_requested"}
            onClick={() => cancelDownload(activeDownload)}
          >
            <X size={18} weight="bold" />
            {activeDownload.status === "cancel_requested" ? "Đang hủy…" : "Hủy tải"}
          </button>
        </section>
      ) : null}

      {resource.loading && !resource.data ? <LoadingState label="Đang đọc Ollama local…" /> : null}
      {resource.error && !resource.data ? (
        <ErrorState error={resource.error} onRetry={resource.reload} />
      ) : null}

      {allModels.length ? (
        <>
          <div className="segment-control model-filter" role="group" aria-label="Lọc model local">
            {[
              ["all", `Đã cài (${allModels.length})`],
              ["active", "Đang dùng"],
              ["fit", "Phù hợp máy"],
            ].map(([value, label]) => (
              <button key={value} className={filter === value ? "is-active" : ""} onClick={() => setFilter(value)}>
                {label}
              </button>
            ))}
          </div>
          {models.length ? <section className="model-grid">
            {models.map((model) => (
              <article className="model-card" key={model.name}>
                <div className="model-card-head">
                  <Cpu size={30} weight="fill" />
                  <div>
                    <h3>{model.name}</h3>
                    <span>{model.family || "Ollama model"}</span>
                  </div>
                  <Badge
                    tone={
                      model.is_chat_model || model.is_embedding_model ? "success" : "paper"
                    }
                  >
                    {model.is_chat_model
                      ? "CHAT ACTIVE"
                      : model.is_embedding_model
                        ? "EMBED ACTIVE"
                        : "INSTALLED"}
                  </Badge>
                </div>
                <dl className="model-meta">
                  <div><dt>Loại model</dt><dd>{humanize(model.model_type)}</dd></div>
                  <div><dt>Capability</dt><dd>{(model.capabilities || []).join(" · ") || "—"}</dd></div>
                  <div><dt>Kích thước</dt><dd>{model.size}</dd></div>
                  <div><dt>RAM ước tính</dt><dd>{formatBytes(model.estimated_ram_bytes)}</dd></div>
                  <div><dt>VRAM ước tính</dt><dd>{formatBytes(model.estimated_vram_bytes)}</dd></div>
                  <div><dt>Độ phù hợp</dt><dd>{humanize(model.fit)}</dd></div>
                  <div><dt>Tham số</dt><dd>{model.parameter_size || "—"}</dd></div>
                  <div><dt>Quantization</dt><dd>{model.quantization_level || "—"}</dd></div>
                </dl>
                <footer className="model-card-actions">
                  <span>Cập nhật {formatDate(model.modified_at)}</span>
                  <details>
                    <summary>Xem chi tiết</summary>
                    <div>
                      <span>Dùng gần nhất: {formatDate(model.last_used_at)}</span>
                      <span>Nhóm đang dùng: {formatNumber(model.assigned_groups)}</span>
                      <span>{model.compatibility?.reason || "Không có ghi chú capability."}</span>
                    </div>
                  </details>
                  <button
                    className="button button--small button--danger"
                    disabled={
                      Boolean(busy) || model.is_chat_model || model.is_embedding_model
                    }
                    onClick={() => previewDelete(model.name)}
                  >
                    <Trash size={16} />
                    Preview xóa
                  </button>
                </footer>
              </article>
            ))}
          </section> : (
            <EmptyState title="Không có model phù hợp bộ lọc" description="Đổi bộ lọc để xem các model đã cài." />
          )}

          <section className="panel">
            <PanelHeader
              eyebrow="ACTIVATION · LIVE"
              title="Chọn model hoạt động"
              action={<Badge tone="yellow">REINDEX-AWARE</Badge>}
            />
            <div className="ops-form-grid">
              <label className="ops-field">
                <span>Chat model</span>
                <select value={chatModel} onChange={(event) => setChatModel(event.target.value)}>
                  {chatModels.map((model) => (
                    <option key={model.name}>{model.name}</option>
                  ))}
                </select>
              </label>
              <label className="ops-field">
                <span>Embedding model</span>
                <select
                  value={embeddingModel}
                  onChange={(event) => setEmbeddingModel(event.target.value)}
                >
                  {embeddingModels.map((model) => (
                    <option key={model.name}>{model.name}</option>
                  ))}
                </select>
              </label>
              <button
                ref={activationTriggerRef}
                className="button button--primary"
                disabled={Boolean(busy) || activationUncertain || resource.loading || Boolean(resource.error) || !chatModel || !embeddingModel}
                onClick={previewActivation}
              >
                <CheckCircle size={18} />
                {busy === "activation-preview" ? "Đang tính ảnh hưởng…" : "Xem ảnh hưởng"}
              </button>
            </div>
            <div className="ops-local-warning ops-local-warning--yellow">
              <Warning size={22} weight="fill" />
              <b>Embedding impact</b>
              <span>
                Backend tự xác định vector dimension và tách kho Qdrant theo embedding model.
              </span>
            </div>
          </section>
        </>
      ) : resource.data ? (
        <EmptyState
          title="Chưa có model Ollama"
          description="Nhập tên model phía trên để tạo background job tải model."
        />
      ) : null}

      {activationUncertain && !activationPreview ? <div><ErrorState error={activationError} /><button ref={activationRecoveryRef} className="button button--outline" disabled={Boolean(busy)} onClick={reconcileActivation}>Đối chiếu cấu hình model</button></div> : null}
      {activationPreview ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !busy) {
              setActivationPreview(null);
            }
          }}
        >
          <section
            ref={activationDialogRef}
            className="modal activation-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="activation-title"
            aria-describedby="activation-description"
            tabIndex={-1}
          >
            <header className="modal-header">
              <div>
                <p className="eyebrow">ACTIVATION IMPACT PREVIEW</p>
                <h2 id="activation-title">Xác nhận thay model Ollama</h2>
              </div>
              <button
                className="icon-button"
                aria-label="Đóng"
                disabled={Boolean(busy)}
                onClick={() => setActivationPreview(null)}
              >
                <X size={22} weight="bold" />
              </button>
            </header>
            <div className="modal-body" id="activation-description">
              {activationError ? <ErrorState error={activationError} /> : null}
              <div className="review-summary">
                <div><span>Chat model</span><b>{activationPreview.requested.chat_model}</b></div>
                <div><span>Embedding model</span><b>{activationPreview.requested.embedding_model}</b></div>
                <div>
                  <span>Re-index</span>
                  <Badge tone={activationPreview.requires_reindex ? "yellow" : "success"}>
                    {activationPreview.requires_reindex ? "BẮT BUỘC" : "KHÔNG"}
                  </Badge>
                </div>
              </div>
              <div className="activation-impact-grid">
                <div><span>Nguồn ảnh hưởng</span><b>{formatNumber(activationPreview.affected_sources)}</b></div>
                <div><span>Vector hiện có</span><b>{formatNumber(activationPreview.affected_vectors)}</b></div>
              </div>
              <div className="ops-local-warning ops-local-warning--yellow">
                <Warning size={22} weight="fill" />
                <b>Ảnh hưởng kích hoạt</b>
                <span>{activationPreview.message}</span>
              </div>
            </div>
            <footer className="modal-actions modal-footer">
              {activationUncertain ? <button className="button button--outline" disabled={Boolean(busy)} onClick={reconcileActivation}>Đối chiếu cấu hình model</button> : null}
              <button
                className="button button--outline"
                disabled={Boolean(busy)}
                onClick={() => setActivationPreview(null)}
              >
                Hủy
              </button>
              <button
                className="button button--primary"
                disabled={Boolean(busy) || activationUncertain}
                onClick={activate}
              >
                {busy === "activate" ? "Đang kích hoạt…" : "Xác nhận kích hoạt"}
              </button>
            </footer>
          </section>
        </div>
      ) : null}
    </section>
  );
}
