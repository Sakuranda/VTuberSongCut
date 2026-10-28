import { useRef, useState } from "react";
import { api } from "./api";
import type { DetectorDevice, ModelCatalog, ModelInfo, Project } from "./api";
import "./model-controls.css";

export function ModelSelector({
  project,
  catalog,
  disabled = false,
  trainingEnabled = true,
  canImport = true,
  onChange,
  onTrain,
  onImport,
}: {
  project: Project | null;
  catalog: ModelCatalog | null;
  disabled?: boolean;
  trainingEnabled?: boolean;
  canImport?: boolean;
  onChange: (patch: {
    detector_model_id?: string | null;
    detector_device?: DetectorDevice;
  }) => void;
  onTrain: () => void;
  onImport: () => void;
}) {
  const [open, setOpen] = useState(false);
  const pinned = useRef(false);
  const help = useRef<HTMLButtonElement>(null);
  const selected = catalog?.models.find(
    (model) => model.id === project?.detector_model_id,
  );
  return (
    <section className="voice-model-panel" aria-label="切歌使用的模型">
      <div className="voice-model-heading">
        <h3>主播模型</h3>
        <div
          className="voice-model-help"
          onMouseEnter={() => setOpen(true)}
          onMouseLeave={() => {
            if (!pinned.current) setOpen(false);
          }}
          onFocus={() => setOpen(true)}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) {
              pinned.current = false;
              setOpen(false);
            }
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.stopPropagation();
              pinned.current = false;
              help.current?.focus();
              setOpen(false);
            }
          }}
        >
          <button
            ref={help}
            type="button"
            className="voice-help-button"
            aria-label="模型帮助"
            aria-expanded={open}
            aria-controls="voice-model-help"
            onClick={() => {
              pinned.current = !pinned.current;
              setOpen(pinned.current);
            }}
          >
            ?
          </button>
          {open && (
            <div
              id="voice-model-help"
              className="voice-help-popover"
              role="dialog"
              aria-label="模型帮助内容"
            >
              <strong>模型告诉拾歌：要找谁的歌声。</strong>
              <p>
                已有模型就直接选择。要剪其他主播的录播，可以用该主播的素材训练一个新模型。
              </p>
              <button
                type="button"
                className="button primary full"
                onClick={() => {
                  pinned.current = false;
                  setOpen(false);
                  onTrain();
                }}
              >
                {trainingEnabled ? "去训练自己的模型" : "查看模型使用方式"}
              </button>
              <a href="/help/faq#models" target="_blank" rel="noreferrer">
                查看使用说明 ↗
              </a>
            </div>
          )}
        </div>
      </div>
      <label className="field">
        选择主播模型
        <select
          value={project?.detector_model_id || ""}
          disabled={disabled || !project || !catalog}
          onChange={(event) =>
            onChange({ detector_model_id: event.target.value || null })
          }
        >
          <option value="">
            {catalog ? "请选择对应主播的模型" : "正在读取模型…"}
          </option>
          {catalog && project?.detector_model_id && !selected && (
            <option value={project.detector_model_id}>已选模型不可用</option>
          )}
          {catalog?.models.map((model) => (
            <option key={model.id} value={model.id} disabled={!model.available}>
              {model.name}
              {model.available ? "" : "（不可用）"}
            </option>
          ))}
        </select>
      </label>
      {!project && <p className="voice-note">先导入录播，再为它选择模型。</p>}
      {project && !project.detector_model_id && (
        <p className="voice-note">选择模型并保存后，就可以检测演唱片段。</p>
      )}
      {selected && (
        <p className="voice-note">
          {selected.origin === "preconfigured"
            ? "预置模型，请确认适用于本次演唱者。"
            : selected.origin === "trained"
              ? "使用你在训练工作区生成的模型。"
              : "使用已导入并通过格式校验的模型。"}
        </p>
      )}
      {selected && (
        <details className="voice-model-details">
          <summary>模型信息</summary>
          <p>
            {selected.architecture.toUpperCase()} ·{" "}
            {(selected.size_bytes / 1048576).toFixed(1)} MB
          </p>
          <p title={selected.sha256}>校验：{selected.sha256.slice(0, 16)}…</p>
        </details>
      )}
      <label className="field">
        运行设备
        <select
          value={project?.detector_device || "auto"}
          disabled={disabled || !project || !catalog}
          onChange={(event) =>
            onChange({ detector_device: event.target.value as DetectorDevice })
          }
        >
          {!catalog?.devices.length && <option value="auto">自动选择</option>}
          {catalog?.devices.map((device) => (
            <option
              key={device.id}
              value={device.id}
              disabled={!device.available}
            >
              {device.label}
              {device.available ? "" : "（不可用）"}
            </option>
          ))}
        </select>
      </label>
      {catalog && !catalog.engine_ready && (
        <p className="voice-model-error" role="status">
          {catalog.engine_error || "模型环境尚未就绪，可以先手动剪辑。"}
        </p>
      )}
      {catalog &&
        project?.detector_model_id &&
        (!selected || !selected.available) && (
          <p className="voice-model-error" role="status">
            {selected?.unavailable_reason || "请重新选择模型。"}
          </p>
        )}
      <div className="voice-model-entrypoints">
        {canImport && (
          <button
            className="voice-text-button"
            type="button"
            onClick={onImport}
          >
            导入模型
          </button>
        )}
        <button className="voice-text-button" type="button" onClick={onTrain}>
          {trainingEnabled ? "训练模型 →" : "模型管理 →"}
        </button>
      </div>
    </section>
  );
}

export function ModelManager({
  catalog,
  onImported,
  onUse,
}: {
  catalog: ModelCatalog | null;
  onImported: (model: ModelInfo) => Promise<void> | void;
  onUse: (model: ModelInfo) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  async function importModel() {
    if (!file) return;
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const model = await api.uploadModel(file, name.trim());
      await onImported(model);
      setFile(null);
      setName("");
      if (input.current) input.current.value = "";
      setMessage(`“${model.name}”已通过格式校验，可以用于切歌。`);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "导入失败，请检查模型文件",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="panel voice-library"
      aria-label="模型管理"
      id="model-library"
      tabIndex={-1}
    >
      <div className="voice-library-header">
        <div>
          <h2>我的模型</h2>
          <p>训练完成的模型会出现在这里，也可以导入已有模型。</p>
        </div>
        <a href="/help/faq#models" target="_blank" rel="noreferrer">
          模型使用帮助 ↗
        </a>
      </div>
      <div className="voice-import-form">
        <label className="field">
          已有模型文件
          <input
            ref={input}
            type="file"
            accept=".pt,.pth"
            disabled={busy}
            onChange={(event) => {
              setFile(event.target.files?.[0] || null);
              setError("");
              setMessage("");
            }}
          />
        </label>
        <label className="field">
          模型名称（选填）
          <input
            value={name}
            maxLength={100}
            disabled={busy}
            placeholder="例如：小歌 · 第一版"
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <button
          type="button"
          className="button"
          disabled={busy || !file}
          onClick={() => void importModel()}
        >
          {busy ? "正在导入并校验…" : "导入已有模型"}
        </button>
      </div>
      <p className="voice-note">支持 AutoSlicer .pt / .pth 检测模型。</p>
      {message && (
        <p className="voice-model-success" role="status">
          {message}
        </p>
      )}
      {error && (
        <p className="voice-model-error" role="alert">
          {error}
        </p>
      )}
      {!catalog ? (
        <p className="voice-note">正在读取模型库…</p>
      ) : catalog.models.length === 0 ? (
        <p className="voice-library-empty">
          还没有模型。完成上面的训练，或导入已有模型。
        </p>
      ) : (
        <div className="voice-model-list">
          {catalog.models.map((model) => (
            <article className="voice-model-card" key={model.id}>
              <div>
                <h3>{model.name}</h3>
                <p>
                  {model.origin === "trained"
                    ? "自行训练"
                    : model.origin === "preconfigured"
                      ? "预置参考模型"
                      : "已导入"}{" "}
                  · {(model.size_bytes / 1048576).toFixed(1)} MB
                </p>
                <details>
                  <summary>查看模型信息</summary>
                  <p>
                    {model.architecture.toUpperCase()} ·{" "}
                    {model.classes === 1
                      ? "目标演唱检测"
                      : `${model.classes} 类声音检测`}
                  </p>
                  <code>{model.sha256}</code>
                </details>
                {!model.available && (
                  <p className="voice-model-error">
                    {model.unavailable_reason}
                  </p>
                )}
              </div>
              <div className="voice-model-actions">
                <button
                  className="button primary"
                  type="button"
                  disabled={busy || !model.available}
                  onClick={() => onUse(model)}
                >
                  用此模型去切歌
                </button>
                {model.available && (
                  <a
                    href={`/api/models/${encodeURIComponent(model.id)}/download`}
                  >
                    导出模型
                  </a>
                )}
              </div>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
