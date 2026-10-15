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
  const [preview, setPreview] = useState<Job | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [version, setVersion] = useState(0);
  const dragStart = useRef<number | null>(null);
  useEffect(() => {
    if (!active) video.current?.pause();
  }, [active]);
  useEffect(() => {
    if (!preview || !["queued", "running"].includes(preview.status)) return;
    let stopped = false;
    const timer = window.setInterval(async () => {
      try {
        const jobs = await api.jobs();
        const current = jobs.find((job) => job.id === preview.id);
        if (!current || stopped) return;
        setPreview(current);
        if (current.status === "succeeded") {
          setVersion(Date.now());
          setMediaError(false);
        }
      } catch {
        /* Poll again without discarding local annotations. */
      }
    }, 2000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [preview?.id, preview?.status]);
  const waitingPreview =
    previewBusy ||
    (!!preview && ["queued", "running"].includes(preview.status));
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
  return (
    <div className="training-annotation">
      <div className="training-preview">
        <video
          ref={video}
          key={`${sample.id}-${version}`}
          src={`${api.trainingMedia(dataset.id, sample.id)}?preview=${version}`}
          controls
          preload="metadata"
          onTimeUpdate={(event) =>
            setNow(Math.round(event.currentTarget.currentTime * 1000))
          }
          onError={() => setMediaError(true)}
          onLoadedData={() => setMediaError(false)}
        />
        {mediaError && (
          <div className="training-media-error">
            <strong>浏览器无法直接播放此录播</strong>
            <p>生成兼容预览后继续标注，训练仍使用原始素材。</p>
            <button
              className="button"
              disabled={waitingPreview}
              onClick={async () => {
                setPreviewBusy(true);
                setError("");
                try {
                  setPreview(await api.trainingPreview(dataset.id, sample.id));
                } catch (e) {
                  setError(e instanceof Error ? e.message : "无法生成预览");
                } finally {
                  setPreviewBusy(false);
                }
              }}
            >
              {waitingPreview ? "正在生成预览…" : "生成兼容预览"}
            </button>
          </div>
        )}
      </div>
      {preview && (
        <p
          className={
            preview.status === "failed" || preview.status === "interrupted"
              ? "model-warning"
              : "training-hint"
          }
          role="status"
        >
          {preview.message}
        </p>
      )}
      <div className="annotation-heading">
        <span>在时间轴拖出区间，或用下方时间戳精确标注。</span>
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
        onKeyDown={(event) => {
          if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
            event.preventDefault();
            seek(now + (event.key === "ArrowRight" ? 1 : -1) * 1000);
          }
        }}
        onPointerDown={(event) => {
          if (disabled || event.button !== 0) return;
          event.preventDefault();
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
      <div className="annotation-controls">
        <label className="field">
          开始时间
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
            onClick={() => setStart(now)}
          >
            用当前播放位置作开始
          </button>
        </label>
        <label className="field">
          结束时间
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
            onClick={() => setEnd(now)}
          >
            用当前播放位置作结束
          </button>
        </label>
      </div>
      <div className="annotation-add">
        <label className="field">
          这段是谁在唱？
          <select
            disabled={disabled}
            value={label}
            onChange={(event) =>
              setLabel(event.target.value as TrainingRegion["label"])
            }
          >
            <option value="target">{dataset.target_name}在唱歌</option>
            <option value="other">其他人 / 原唱在唱歌</option>
          </select>
        </label>
        <button
          className="button primary"
          disabled={
            disabled || Object.values(invalid).some(Boolean) || start >= end
          }
          onClick={addRegion}
        >
          添加这段标注
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
            还没有标注。先找到一段{dataset.target_name}
            的演唱，标记它的开始和结束。
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
