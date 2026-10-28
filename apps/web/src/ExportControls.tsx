import type { ExportSettings } from "./api";

interface Props {
  value: ExportSettings;
  disabled: boolean;
  hasVideo?: boolean;
  lowResource?: boolean;
  sourceHeight?: number | null;
  sourceFps?: number | null;
  subtitleEnabled?: boolean;
  onChange: (value: ExportSettings) => void;
}
export function ExportControls({
  value,
  disabled,
  onChange,
  hasVideo = true,
  lowResource = false,
  sourceHeight,
  sourceFps,
  subtitleEnabled = false,
}: Props) {
  const update = (patch: Partial<ExportSettings>) =>
    onChange({ ...value, ...patch });
  const fast = value.mode === "fast";
  const fastTooLarge =
    lowResource && ((sourceHeight || 0) > 1080 || (sourceFps || 0) > 30);
  return (
    <div className="export-controls">
      <fieldset className="format-picks" disabled={disabled}>
        <legend>输出文件</legend>
        <label>
          <input
            type="checkbox"
            checked={hasVideo && value.video}
            disabled={!hasVideo}
            onChange={(event) => update({ video: event.target.checked })}
          />
          <span>
            <strong>MP4 视频</strong>
            <small>{hasVideo ? "画面与声音" : "素材仅含音频"}</small>
          </span>
        </label>
        <label>
          <input
            type="checkbox"
            checked={value.audio}
            onChange={(event) => update({ audio: event.target.checked })}
          />
          <span>
            <strong>MP3 音频</strong>
            <small>单独保存声音</small>
          </span>
        </label>
      </fieldset>
      {!value.video && !value.audio && (
        <p className="settings-error" role="alert">
          请至少选择一种输出文件。
        </p>
      )}
      {hasVideo && (
        <label className="field">
          导出方式
          <select
            aria-label="导出方式"
            disabled={disabled || !value.video}
            value={value.mode || "precise"}
            onChange={(event) => {
              const mode = event.target.value as "precise" | "fast";
              update(
                mode === "fast"
                  ? {
                      mode,
                      resolution: "source",
                      fps: null,
                      video_codec: "h264",
                    }
                  : { mode },
              );
            }}
          >
            <option value="precise">精确导出</option>
            <option value="fast" disabled={fastTooLarge}>
              快速导出（按关键帧裁切）
            </option>
          </select>
          <small>
            {fast
              ? "保留原始画质，切点受关键帧影响，不烧录字幕。"
              : "精确调整切点，可更改画质并烧录字幕。"}
          </small>
        </label>
      )}
      {fast && subtitleEnabled && value.video && (
        <p className="settings-error" role="alert">
          快速导出不能烧录字幕，请在「歌词与字幕」中关闭烧录，或切换精确导出。
        </p>
      )}
      {lowResource && (
        <p className="export-note">
          服务器支持最高 1080p、30fps、H.264 导出。更高规格请在本地电脑完成。
        </p>
      )}
      <fieldset
        className="video-settings"
        hidden={!hasVideo}
        disabled={disabled || !value.video || !hasVideo || fast}
      >
        <legend>视频规格</legend>
        <label className="field">
          视频编码
          <select
            value={value.video_codec}
            onChange={(event) =>
              update({
                video_codec: event.target
                  .value as ExportSettings["video_codec"],
              })
            }
          >
            <option value="h264">H.264 · 兼容性好</option>
            <option value="hevc" disabled={lowResource}>
              HEVC / H.265 · 文件更小
            </option>
          </select>
        </label>
        <label className="field">
          分辨率
          <select
            value={value.resolution}
            onChange={(event) =>
              update({
                resolution: event.target.value as ExportSettings["resolution"],
              })
            }
          >
            <option
              value="source"
              disabled={lowResource && (sourceHeight || 0) > 1080}
            >
              保留原始分辨率
            </option>
            <option value="720p">720p</option>
            <option value="1080p">1080p</option>
            <option value="2160p" disabled={lowResource}>
              4K / 2160p
            </option>
          </select>
        </label>
        <label className="field">
          输出帧率
          <select
            value={value.fps ?? "source"}
            onChange={(event) =>
              update({
                fps:
                  event.target.value === "source"
                    ? null
                    : (Number(event.target.value) as ExportSettings["fps"]),
              })
            }
          >
            <option
              value="source"
              disabled={lowResource && (sourceFps || 0) > 30}
            >
              保留原始帧率
            </option>
            {[24, 25, 30, 50, 60].map((fps) => (
              <option key={fps} value={fps} disabled={lowResource && fps > 30}>
                {fps} fps
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          视频压缩方式
          <select
            value={value.rate_control}
            onChange={(event) =>
              update({
                rate_control: event.target
                  .value as ExportSettings["rate_control"],
              })
            }
          >
            <option value="quality">按画质输出 · CRF</option>
            <option value="bitrate">按目标码率输出</option>
          </select>
        </label>
        {value.rate_control === "quality" ? (
          <label className="field">
            画质值（CRF）
            <input
              type="number"
              min={0}
              max={40}
              step={1}
              value={value.crf}
              onChange={(event) => update({ crf: Number(event.target.value) })}
            />
            <small>0–40，数值越小画质越高，文件通常越大。</small>
          </label>
        ) : (
          <label className="field">
            目标视频码率（kbps）
            <input
              type="number"
              min={250}
              max={100000}
              step={250}
              value={value.video_bitrate_kbps}
              onChange={(event) =>
                update({ video_bitrate_kbps: Number(event.target.value) })
              }
            />
            <small>250–100000 kbps。8000 kbps 约为 8 Mbps。</small>
          </label>
        )}
      </fieldset>
      <label className="field">
        音频码率
        <select
          disabled={disabled}
          value={value.audio_bitrate_kbps}
          onChange={(event) =>
            update({
              audio_bitrate_kbps: Number(
                event.target.value,
              ) as ExportSettings["audio_bitrate_kbps"],
            })
          }
        >
          {[64, 96, 128, 160, 192, 256, 320].map((bitrate) => (
            <option key={bitrate} value={bitrate}>
              {bitrate} kbps
            </option>
          ))}
        </select>
        <small>
          {fast
            ? "用于单独导出的 MP3；快速导出视频保留原音轨。"
            : "用于视频中的声音与单独导出的 MP3。"}
        </small>
      </label>
      <p className="export-note">
        设置保存在当前歌曲片段中。修改后先保存，再开始导出。
      </p>
    </div>
  );
}
