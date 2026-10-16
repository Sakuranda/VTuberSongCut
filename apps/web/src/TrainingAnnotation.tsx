import { useEffect, useRef, useState } from "react";
import { api } from "./api";
import type {
  Job,
  TrainingDataset,
  TrainingRegion,
  TrainingSample,
} from "./api";
import { formatTimecode as format } from "./editing";
import { TimecodeInput } from "./TimecodeInput";
import { OperationProgress } from "./OperationProgress";

export function TrainingAnnotation({
  active,
  dataset,
  sample,
  disabled,
  onChange,
}: {
  active: boolean;
  dataset: TrainingDataset;
  sample: TrainingSample;
  disabled: boolean;
  onChange: (sample: TrainingSample) => void;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [now, setNow] = useState(0);
  const [start, setStart] = useState(0);
  const [end, setEnd] = useState(Math.min(sample.duration_ms, 10000));
  const [label, setLabel] = useState<TrainingRegion["label"]>("target");
  const [invalid, setInvalid] = useState<Record<string, boolean>>({});
  const [error, setError] = useState("");
  const [mediaError, setMediaError] = useState(false);
  const [mediaReady, setMediaReady] = useState(false);
  const [networkError, setNetworkError] = useState(false);
  const [preview, setPreview] = useState<Job | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewRestored, setPreviewRestored] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [pollError, setPollError] = useState("");
  const [version, setVersion] = useState("");
  const dragStart = useRef<number | null>(null);
  useEffect(() => {
    if (!active) video.current?.pause();
  }, [active]);
  useEffect(() => {
    let stopped = false;
    let timer: number | undefined;
    async function poll() {
      let retry = false;
      try {
        const jobs = await api.jobs(`training-media:${sample.id}`);
        if (stopped) return;
        const current = jobs.find(
          (job) =>
            job.project_id === `training-media:${sample.id}` &&
            job.kind === "training_preview",
        );
        setPollError("");
        if (current) {
          setPreview(current);
          retry = ["queued", "running"].includes(current.status);
          if (current.status === "succeeded") {
            setVersion(current.id);
            setMediaError(false);
            setNetworkError(false);
          }
        }
      } catch {
        if (!stopped)
          setPollError(
            "暂时无法读取预览进度，正在重试。后台任务不会因此停止，请勿重复提交。",
          );
        retry = true;
      } finally {
        if (!stopped) {
          setPreviewRestored(true);
          if (retry) timer = window.setTimeout(poll, 2000);
        }
      }
    }
    void poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [dataset.id, sample.id, preview?.id]);
  const waitingPreview =
    previewBusy ||
    (!!preview && ["queued", "running"].includes(preview.status));
  const hasPreview =
    sample.preview_available || preview?.status === "succeeded";
  const flv = /\.flv$/i.test(sample.name);
  const showCompatibility = mediaError || (flv && !hasPreview && !mediaReady);
  const previewFailed =
    preview?.status === "failed" || preview?.status === "interrupted";
  async function generatePreview() {
    setPreviewBusy(true);
    setPreviewError("");
    try {
      setPreview(await api.trainingPreview(dataset.id, sample.id));
    } catch (e) {
      setPreviewError(
        e instanceof Error ? e.message : "无法生成预览，请重试。",
      );
    } finally {
      setPreviewBusy(false);
    }
  }
  function seek(ms: number) {
    const next = Math.max(0, Math.min(sample.duration_ms, Math.round(ms)));
    setNow(next);
    if (video.current) video.current.currentTime = next / 1000;
  }
  function pointerTime(event: React.PointerEvent<HTMLDivElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    return Math.round(
      Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)) *
        sample.duration_ms,
    );
  }
  function addRegion() {
    if (start >= end) {
      setError("结束时间须晚于开始时间。");
      return;
    }
    if (
      sample.regions.some(
        (region) => start < region.end_ms && end > region.start_ms,
      )
    ) {
      setError("这段时间与已有标注重叠，请调整起止时间或删除旧标注。");
      return;
    }
    onChange({
      ...sample,
      reviewed: false,
      regions: [
        ...sample.regions,
        { start_ms: start, end_ms: end, label },
      ].sort((a, b) => a.start_ms - b.start_ms),
    });
    setError("");
  }
  function markBoundary(edge: "start" | "end") {
    const position = Math.max(
      0,
      Math.min(
        sample.duration_ms,
        Math.round((video.current?.currentTime ?? now / 1000) * 1000),
      ),
    );
    if (edge === "start") setStart(position);
    else setEnd(position);
  }
  useEffect(() => {
    function shortcuts(event: KeyboardEvent) {
      const target = event.target instanceof Element ? event.target : null;
      const player = video.current;
      if (
        !active ||
        disabled ||
        !player ||
        player.readyState < 1 ||
        player.error ||
        showCompatibility ||
        event.defaultPrevented ||
        event.isComposing ||
        event.keyCode === 229 ||
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        target?.closest(
          'input, textarea, select, button, a, summary, [role="textbox"], [role="combobox"], [contenteditable]:not([contenteditable="false"])',
        ) ||
        document.querySelector(
          'dialog[open], [aria-modal="true"], [role="dialog"]',
        )
      )
        return;

      const key = event.key.toLowerCase();
      const arrow = event.key === "ArrowLeft" || event.key === "ArrowRight";
      const toggle = event.code === "Space" || event.key === " " || key === "k";
      if (!arrow && !toggle && !["i", "o", "a"].includes(key)) return;
      // Capture before native video shortcuts to avoid double seeks/toggles.
      event.preventDefault();
      if (event.repeat && !arrow) return;
      if (arrow) {
        player.pause();
        seek(
          player.currentTime * 1000 +
            (event.key === "ArrowLeft" ? -1 : 1) *
              (event.shiftKey ? 10000 : 1000),
        );
      } else if (toggle) {
        if (player.paused)
          void player
            .play()
            .catch(() =>
              setError("无法开始播放，请点击播放器中的播放按钮重试。"),
            );
        else player.pause();
      } else if (key === "i" || key === "o") {
        markBoundary(key === "i" ? "start" : "end");
      } else if (Object.values(invalid).some(Boolean)) {
        setError("请先修正开始或结束时间，再添加标注。");
      } else {
        addRegion();
      }
    }
    window.addEventListener("keydown", shortcuts, { capture: true });
    return () =>
      window.removeEventListener("keydown", shortcuts, { capture: true });
  }, [
    active,
    disabled,
    showCompatibility,
    sample,
    start,
    end,
    label,
    invalid,
    now,
    onChange,
  ]);
  return (
    <div className="training-annotation">
      <div className="training-preview">
        <video
          ref={video}
          key={`${sample.id}-${version}`}
          src={`${api.trainingMedia(dataset.id, sample.id)}?preview=${version}`}
          controls
          aria-label="训练素材预览"
          tabIndex={0}
          preload="metadata"
          onTimeUpdate={(event) =>
            setNow(Math.round(event.currentTarget.currentTime * 1000))
          }
          onError={(event) => {
            setMediaError(true);
            setMediaReady(false);
            setNetworkError(event.currentTarget.error?.code === 2);
          }}
          onLoadedData={() => {
            setMediaError(false);
            setNetworkError(false);
            setMediaReady(true);
          }}
        />
        {showCompatibility && (
          <div className="training-media-error">
            <strong>
              {networkError
                ? "视频读取中断"
                : flv
                  ? "FLV 需要兼容预览来播放与标注"
                  : "当前浏览器无法直接播放此录播"}
            </strong>
            <p>
              {networkError
                ? "请检查服务连接后重新加载视频。"
                : "生成可播放的预览副本后继续标注，训练仍读取原始素材。"}
            </p>
            <button
              className="button primary preview-action"
              disabled={disabled || waitingPreview || !previewRestored}
              onClick={() =>
                networkError ? video.current?.load() : void generatePreview()
              }
            >
              {waitingPreview
                ? "正在生成预览…"
                : !previewRestored
                  ? "正在检查预览状态…"
                  : networkError
                    ? "重新加载视频"
                    : previewFailed
                      ? "重新生成兼容预览"
                      : "生成兼容预览"}
            </button>
          </div>
        )}
      </div>
      <details className="training-keyboard-help">
        <summary>
          快捷键 <span>空格播放 · I / O 标记 · A 添加</span>
        </summary>
        <div
          className="shortcut-guide training-shortcuts"
          aria-label="训练视频快捷键"
        >
          <span>
            <kbd>空格</kbd> / <kbd>K</kbd> 播放 / 暂停
          </span>
          <span>
            <kbd>←</kbd>
            <kbd>→</kbd> 前后 1 秒
          </span>
          <span>
            <kbd>Shift</kbd> + <kbd>←</kbd>
            <kbd>→</kbd> 前后 10 秒
          </span>
          <span>
            <kbd>I</kbd> 开始 · <kbd>O</kbd> 结束 · <kbd>A</kbd> 添加
          </span>
        </div>
        <p className="training-shortcut-note">
          点击视频或时间轴后使用；输入时不会触发快捷键。
        </p>
      </details>
      {(previewBusy || (preview && preview.status !== "succeeded")) && (
        <OperationProgress
          title={
            previewBusy
              ? "正在提交预览任务"
              : preview?.status === "queued"
                ? "预览任务等待中"
                : preview?.status === "succeeded"
                  ? "兼容预览已生成"
                  : previewFailed
                    ? "兼容预览未完成"
                    : "正在生成兼容预览"
          }
          percent={
            previewBusy
              ? null
              : preview?.status === "succeeded"
                ? 100
                : typeof preview?.progress === "number"
                  ? Math.min(
                      99,
                      Math.floor(Math.max(0, preview.progress) * 100),
                    )
                  : null
          }
          failed={previewFailed}
          detail={
            previewBusy ? "正在连接服务…" : preview?.message || "正在准备预览…"
          }
        />
      )}
      {previewError && (
        <p className="training-inline-error" role="alert">
          {previewError}
        </p>
      )}
      {pollError && (
        <p className="training-inline-error" role="status">
          {pollError}
        </p>
      )}
      <div className="annotation-heading">
        <span>拖动选择区间</span>
        <code>{format(now)}</code>
      </div>
      <div
        className="annotation-track"
        aria-label="拖动选择标注区间"
        role="slider"
        tabIndex={0}
        aria-valuemin={0}
        aria-valuemax={sample.duration_ms}
        aria-valuenow={now}
        aria-valuetext={format(now)}
        onPointerDown={(event) => {
          if (disabled || event.button !== 0) return;
          event.preventDefault();
          event.currentTarget.focus();
          event.currentTarget.setPointerCapture(event.pointerId);
          const ms = pointerTime(event);
          dragStart.current = ms;
          seek(ms);
        }}
        onPointerMove={(event) => {
          if (
            !event.currentTarget.hasPointerCapture(event.pointerId) ||
            dragStart.current === null
          )
            return;
          const ms = pointerTime(event);
          setStart(Math.min(ms, dragStart.current));
          setEnd(Math.max(ms, dragStart.current));
          setInvalid({});
        }}
        onPointerUp={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId))
            event.currentTarget.releasePointerCapture(event.pointerId);
          dragStart.current = null;
        }}
      >
        {sample.regions.map((region, index) => (
          <span
            key={index}
            className={`annotation-band ${region.label}`}
            style={{
              left: `${(region.start_ms / sample.duration_ms) * 100}%`,
              width: `${((region.end_ms - region.start_ms) / sample.duration_ms) * 100}%`,
            }}
          />
        ))}
        <span
          className="annotation-range"
          style={{
            left: `${(start / sample.duration_ms) * 100}%`,
            width: `${(Math.max(0, end - start) / sample.duration_ms) * 100}%`,
          }}
        />
        <span
          className="annotation-playhead"
          style={{ left: `${(now / sample.duration_ms) * 100}%` }}
        />
      </div>
      <div className="annotation-scale">
        <code>00:00:00</code>
        <code>{format(sample.duration_ms)}</code>
      </div>
      <div className="annotation-controls" aria-label="标注区间">
        <div className="field">
          <label htmlFor={`train-start-${sample.id}`}>开始时间</label>
          <TimecodeInput
            id={`train-start-${sample.id}`}
            value={start}
            min={0}
            max={sample.duration_ms}
            disabled={disabled}
            onChange={setStart}
            onValidity={(value) =>
              setInvalid((old) => ({ ...old, start: value }))
            }
          />
          <button
            className="text-action"
            disabled={disabled}
            aria-keyshortcuts="I"
            aria-label="将当前播放位置设为开始时间"
            onClick={() => markBoundary("start")}
          >
            设为当前位置
          </button>
        </div>
        <div className="field">
          <label htmlFor={`train-end-${sample.id}`}>结束时间</label>
          <TimecodeInput
            id={`train-end-${sample.id}`}
            value={end}
            min={0}
            max={sample.duration_ms}
            disabled={disabled}
            onChange={setEnd}
            onValidity={(value) =>
              setInvalid((old) => ({ ...old, end: value }))
            }
          />
          <button
            className="text-action"
            disabled={disabled}
            aria-keyshortcuts="O"
            aria-label="将当前播放位置设为结束时间"
            onClick={() => markBoundary("end")}
          >
            设为当前位置
          </button>
        </div>
        <label className="field annotation-singer">
          演唱者
          <select
            disabled={disabled}
            value={label}
            onChange={(event) =>
              setLabel(event.target.value as TrainingRegion["label"])
            }
          >
            <option value="target">{dataset.target_name}</option>
            <option value="other">其他人 / 原唱</option>
          </select>
        </label>
        <button
          className="button primary"
          aria-keyshortcuts="A"
          disabled={
            disabled || Object.values(invalid).some(Boolean) || start >= end
          }
          onClick={addRegion}
        >
          添加标注
        </button>
      </div>
      {error && (
        <p className="model-warning" role="alert">
          {error}
        </p>
      )}
      <div className="region-list" aria-label="已标注区间">
        {!sample.regions.length && (
          <p className="training-hint">
            选择开始、结束和演唱者，然后添加标注。
          </p>
        )}
        {sample.regions.map((region, index) => (
          <div
            className={`region-row ${region.label}`}
            key={`${region.start_ms}-${index}`}
          >
            <button
              className="region-seek"
              onClick={() => {
                setStart(region.start_ms);
                setEnd(region.end_ms);
                seek(region.start_ms);
              }}
            >
              <strong>
                {region.label === "target"
                  ? `${dataset.target_name}在唱歌`
                  : "其他人 / 原唱在唱歌"}
              </strong>
              <code>
                {format(region.start_ms)} — {format(region.end_ms)}
              </code>
            </button>
            <button
              className="text-action"
              disabled={disabled}
              aria-label={`删除第${index + 1}段标注`}
              onClick={() =>
                onChange({
                  ...sample,
                  reviewed: false,
                  regions: sample.regions.filter((_, i) => i !== index),
                })
              }
            >
              删除
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
