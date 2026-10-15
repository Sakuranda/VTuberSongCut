import type { ExportSettings } from "./api";

interface Props {
  value: ExportSettings;
  disabled: boolean;
  onChange: (value: ExportSettings) => void;
}
export function ExportControls({ value, disabled, onChange }: Props) {
  const update = (patch: Partial<ExportSettings>) =>
    onChange({ ...value, ...patch });
  return (
    <div className="export-controls">
      <div className="inspector-intro">
        <span className="eyebrow">EXPORT SETTINGS</span>
        <h3>选择这首歌的输出规格。</h3>
      </div>
      <fieldset className="format-picks" disabled={disabled}>
        <legend>输出文件</legend>
        <label>
          <input
            type="checkbox"
            checked={value.video}
            onChange={(event) => update({ video: event.target.checked })}
          />
          <span>
            <strong>MP4 视频</strong>
            <small>画面与声音</small>
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
      <fieldset className="video-settings" disabled={disabled || !value.video}>
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
            <option value="hevc">HEVC / H.265 · 文件更小</option>
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
            <option value="source">保留原始分辨率</option>
            <option value="720p">720p</option>
            <option value="1080p">1080p</option>
            <option value="2160p">4K / 2160p</option>
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
            <option value="source">保留原始帧率</option>
            {[24, 25, 30, 50, 60].map((fps) => (
              <option key={fps} value={fps}>
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
        <small>用于视频中的声音与单独导出的 MP3。</small>
      </label>
      <p className="export-note">
        设置保存在当前歌曲片段中。修改后先保存，再开始导出。
      </p>
    </div>
  );
}
