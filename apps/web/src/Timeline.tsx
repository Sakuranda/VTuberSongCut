import { useRef } from "react";
import type { KeyboardEvent, PointerEvent } from "react";
import type { Clip } from "./api";
import { formatTimecode, snapToFrame, stepFrames } from "./editing";

type Edge = "start_ms" | "end_ms";
interface Props {
  duration: number;
  now: number;
  clip?: Clip;
  fps: number;
  disabled: boolean;
  onSeek: (ms: number) => void;
  onBoundary: (edge: Edge, ms: number) => void;
}
export function Timeline({
  duration,
  now,
  clip,
  fps,
  disabled,
  onSeek,
  onBoundary,
}: Props) {
  const track = useRef<HTMLDivElement>(null);
  const percent = (ms: number) =>
    `${Math.max(0, Math.min(100, (ms / Math.max(1, duration)) * 100))}%`;
  function pointerTime(event: PointerEvent) {
    const box = track.current!.getBoundingClientRect();
    return Math.max(
      0,
      Math.min(
        duration,
        snapToFrame(((event.clientX - box.left) / box.width) * duration, fps),
      ),
    );
  }
  function changeBoundary(edge: Edge, value: number) {
    if (!clip || disabled) return;
    const bounded = Math.round(
      Math.max(
        edge === "end_ms" ? clip.start_ms + 1 : 0,
        Math.min(edge === "start_ms" ? clip.end_ms - 1 : duration, value),
      ),
    );
    onBoundary(edge, bounded);
    onSeek(bounded);
  }
  function handleKey(event: KeyboardEvent<HTMLButtonElement>, edge: Edge) {
    if (!clip || disabled || event.ctrlKey || event.altKey || event.metaKey)
      return;
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      event.stopPropagation();
      changeBoundary(
        edge,
        stepFrames(
          clip[edge],
          event.key === "ArrowLeft" ? -1 : 1,
          event.shiftKey ? 10 : 1,
          fps,
        ),
      );
    }
  }
  return (
    <div className="main-timeline">
      <div className="timeline-heading">
        <span>
          {clip
            ? "拖动手柄裁切 · 点击时间轴定位"
            : "点击时间轴定位，添加片段后可拖动裁切"}
        </span>
        <span>{Number(fps.toFixed(3))} fps</span>
      </div>
      <div
        ref={track}
        className="timeline-track"
        onPointerDown={(event) => {
          if (duration <= 0 || event.button !== 0) return;
          event.preventDefault();
          event.currentTarget
            .querySelector<HTMLElement>(".timeline-seek")
            ?.focus();
          event.currentTarget.setPointerCapture(event.pointerId);
          onSeek(pointerTime(event));
        }}
        onPointerMove={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId))
            onSeek(pointerTime(event));
        }}
        onPointerUp={(event) => {
          if (event.currentTarget.hasPointerCapture(event.pointerId))
            event.currentTarget.releasePointerCapture(event.pointerId);
        }}
        onKeyDown={(event) => {
          if (
            event.ctrlKey ||
            event.altKey ||
            event.metaKey ||
            !["ArrowLeft", "ArrowRight"].includes(event.key)
          )
            return;
          event.preventDefault();
          event.stopPropagation();
          onSeek(
            stepFrames(
              now,
              event.key === "ArrowLeft" ? -1 : 1,
              event.shiftKey ? 10 : 1,
              fps,
            ),
          );
        }}
      >
        <div
          className="timeline-seek"
          role="slider"
          tabIndex={duration > 0 ? 0 : -1}
          aria-label="播放位置时间轴"
          aria-valuemin={0}
          aria-valuemax={duration / 1000}
          aria-valuenow={Math.min(now, duration) / 1000}
          aria-valuetext={formatTimecode(now)}
        />
        <div className="timeline-ticks" />
        {clip && (
          <div
            className="timeline-selection"
            style={{
              left: percent(clip.start_ms),
              width: percent(clip.end_ms - clip.start_ms),
            }}
          />
        )}
        <div className="timeline-playhead" style={{ left: percent(now) }} />
        {clip &&
          (["start_ms", "end_ms"] as const).map((edge, index) => (
            <button
              key={edge}
              type="button"
              role="slider"
              className={`timeline-handle ${index === 0 ? "in-handle" : "out-handle"}`}
              style={{ left: percent(clip[edge]) }}
              disabled={disabled}
              aria-label={index === 0 ? "拖动调整入点" : "拖动调整出点"}
              aria-valuemin={(index === 0 ? 0 : clip.start_ms + 1) / 1000}
              aria-valuemax={(index === 0 ? clip.end_ms - 1 : duration) / 1000}
              aria-valuenow={clip[edge] / 1000}
              aria-valuetext={formatTimecode(clip[edge])}
              aria-describedby="handle-keyboard-help"
              onPointerDown={(event) => {
                if (event.button !== 0) return;
                event.preventDefault();
                event.stopPropagation();
                event.currentTarget.focus();
                event.currentTarget.setPointerCapture(event.pointerId);
              }}
              onPointerMove={(event) => {
                event.stopPropagation();
                if (event.currentTarget.hasPointerCapture(event.pointerId))
                  changeBoundary(edge, pointerTime(event));
              }}
              onPointerUp={(event) => {
                event.stopPropagation();
                if (event.currentTarget.hasPointerCapture(event.pointerId))
                  event.currentTarget.releasePointerCapture(event.pointerId);
              }}
              onKeyDown={(event) => handleKey(event, edge)}
            >
              <span className="handle-cap">{index === 0 ? "I" : "O"}</span>
              <span className="handle-time">{formatTimecode(clip[edge])}</span>
            </button>
          ))}
      </div>
      <div className="timeline-labels">
        <code>{formatTimecode(0)}</code>
        <code>{formatTimecode(duration)}</code>
      </div>
      <div className="shortcut-guide">
        <span>
          <kbd>I</kbd> 入点 <kbd>O</kbd> 出点
        </span>
        <span>
          <kbd>←</kbd>
          <kbd>→</kbd> 逐帧 <kbd>Shift</kbd> ×10
        </span>
        <span>
          <kbd>Space</kbd> 播放 / 暂停
        </span>
      </div>
      <p id="handle-keyboard-help" className="handle-help">
        聚焦入点或出点手柄后，← / → 调整该边界；按住 Shift 每次调整 10 帧。
      </p>
    </div>
  );
}
