import { useEffect, useRef, useState } from "react";
import { api, type CacheReport } from "./api";
import "./cache-settings.css";

const bytes = (value: number) =>
  value < 1024 * 1024
    ? `${(value / 1024).toFixed(1)} KB`
    : value < 1024 ** 3
      ? `${(value / 1024 ** 2).toFixed(1)} MB`
      : `${(value / 1024 ** 3).toFixed(2)} GB`;

export function CacheSettings({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const generation = useRef(0);
  const [report, setReport] = useState<CacheReport | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [cleaning, setCleaning] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const node = dialog.current;
    if (open && !node?.open) node?.showModal();
    if (!open && node?.open) node.close();
    const current = ++generation.current;
    if (!open) return;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setNotice("");
    api
      .cache(controller.signal)
      .then((value) => {
        if (generation.current === current) setReport(value);
      })
      .catch((err) => {
        if (!controller.signal.aborted && generation.current === current)
          setError(
            err instanceof Error ? err.message : "无法读取缓存，请重试。",
          );
      })
      .finally(() => {
        if (generation.current === current) setLoading(false);
      });
    return () => {
      controller.abort();
      generation.current++;
    };
  }, [open, attempt]);
  async function clean() {
    const current = generation.current;
    setCleaning(true);
    setError("");
    setNotice("");
    try {
      const result = await api.cleanCache();
      if (current !== generation.current) return;
      setReport(result);
      setNotice(
        `已清理 ${result.removed_files} 个文件，${bytes(result.removed_bytes)}。${result.failed_files ? `另有 ${result.failed_files} 个文件暂时无法清理，请稍后重试。` : ""}`,
      );
    } catch (err) {
      if (current === generation.current)
        setError(
          err instanceof Error ? err.message : "清理未完成，请重新检查缓存。",
        );
    } finally {
      setCleaning(false);
    }
  }
  return (
    <dialog
      ref={dialog}
      className="appearance-dialog cache-dialog"
      aria-labelledby="cache-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <div className="appearance-heading">
        <h2 id="cache-title">缓存管理</h2>
        <button
          className="button appearance-close"
          aria-label="关闭缓存管理"
          onClick={onClose}
        >
          ×
        </button>
      </div>
      <div className="appearance-body">
        <p className="appearance-description">
          清理不再使用的预览和已结束任务的中间数据。
        </p>
        <div className="cache-total" aria-live="polite">
          <span>可清理</span>
          <strong>
            {loading
              ? "正在检查…"
              : report
                ? bytes(report.bytes)
                : "暂时无法读取"}
          </strong>
        </div>
        {report && (
          <dl className="cache-categories">
            {report.categories.map((item) => (
              <div key={item.key}>
                <dt>{item.label}</dt>
                <dd>{bytes(item.bytes)}</dd>
              </div>
            ))}
          </dl>
        )}
        <p>
          原始录播、模型、标注、导出成品和当前使用的预览会保留。正在处理的任务不会被清理。
        </p>
        <p>
          超过 {report?.retention_days ?? 7}{" "}
          天的可清理缓存会在服务启动后及运行期间每小时自动检查回收。
        </p>
        <p className="cache-browser-note">
          视频预览会短暂缓存，方便拖动和回看；页面图片等资源由浏览器管理，这里不会清除皮肤偏好。
        </p>
        {notice && <p role="status">{notice}</p>}
        {error && (
          <div className="banner error" role="alert">
            <span>{error}</span>
          </div>
        )}
      </div>
      <div className="appearance-footer">
        <button
          className="button"
          disabled={loading || cleaning}
          onClick={() => setAttempt((value) => value + 1)}
        >
          重新检查
        </button>
        <button
          className="button primary"
          disabled={loading || cleaning || !!error || !report?.files}
          onClick={() => void clean()}
        >
          {cleaning ? "正在清理…" : "清理缓存"}
        </button>
      </div>
    </dialog>
  );
}
