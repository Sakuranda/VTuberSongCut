import type { ExportSettings, Project } from "./api";

export const DEFAULT_EXPORT_SETTINGS: ExportSettings = {
  mode: "precise",
  video: true,
  audio: true,
  video_codec: "h264",
  resolution: "source",
  fps: null,
  rate_control: "quality",
  crf: 20,
  video_bitrate_kbps: 8000,
  audio_bitrate_kbps: 192,
};
export function normalizeProject(project: Project): Project {
  return {
    ...project,
    source_fps: project.source_fps ?? null,
    detector_model_id: project.detector_model_id ?? null,
    detector_device: project.detector_device ?? "auto",
    clips: project.clips.map((clip) => ({
      ...clip,
      export_settings: { ...DEFAULT_EXPORT_SETTINGS, ...clip.export_settings },
    })),
  };
}
export function formatTimecode(ms: number) {
  const n = Math.max(0, Math.round(ms));
  return `${String(Math.floor(n / 3600000)).padStart(2, "0")}:${String(Math.floor(n / 60000) % 60).padStart(2, "0")}:${String(Math.floor(n / 1000) % 60).padStart(2, "0")}.${String(n % 1000).padStart(3, "0")}`;
}
export function parseTimecode(text: string): number | null {
  const match = /^(\d{2,}):([0-5]\d):([0-5]\d)(?:\.(\d{1,3}))?$/.exec(
    text.trim(),
  );
  if (!match) return null;
  const ms =
    Number(match[1]) * 3600000 +
    Number(match[2]) * 60000 +
    Number(match[3]) * 1000 +
    Number((match[4] || "").padEnd(3, "0"));
  return Number.isSafeInteger(ms) ? ms : null;
}
export const editingFps = (fps: number | null | undefined) =>
  Number.isFinite(fps) && fps! > 0 ? fps! : 25;
// Use source frame indices so repeated steps at 29.97/59.94 fps do not accumulate rounding drift.
export const snapToFrame = (ms: number, fps: number) =>
  Math.round((Math.round((ms * fps) / 1000) * 1000) / fps);
export const stepFrames = (
  ms: number,
  direction: number,
  count: number,
  fps: number,
) =>
  Math.round(
    ((Math.round((ms * fps) / 1000) + direction * count) * 1000) / fps,
  );
export function validateProject(project: Project): string | null {
  for (const [index, clip] of project.clips.entries()) {
    const label = `第 ${index + 1} 个片段`;
    if (
      ![clip.start_ms, clip.end_ms].every(Number.isSafeInteger) ||
      clip.start_ms < 0 ||
      clip.end_ms > project.duration_ms ||
      clip.start_ms >= clip.end_ms
    )
      return `${label}的入点、出点无效，请确保片段在素材范围内且出点晚于入点。`;
    if (
      !Number.isInteger(clip.subtitle.font_size) ||
      clip.subtitle.font_size < 16 ||
      clip.subtitle.font_size > 100 ||
      !Number.isInteger(clip.subtitle.margin_bottom) ||
      clip.subtitle.margin_bottom < 0 ||
      clip.subtitle.margin_bottom > 400
    )
      return `${label}的字幕字号须为 16–100，底部边距须为 0–400 的整数。`;
    const settings = clip.export_settings;
    if (
      !settings ||
      typeof settings.video !== "boolean" ||
      typeof settings.audio !== "boolean" ||
      (!settings.video && !settings.audio)
    )
      return `${label}请至少选择 MP4 视频或 MP3 音频中的一种。`;
    if (
      !["h264", "hevc"].includes(settings.video_codec) ||
      !["source", "720p", "1080p", "2160p"].includes(settings.resolution) ||
      ![null, 24, 25, 30, 50, 60].includes(settings.fps) ||
      !["quality", "bitrate"].includes(settings.rate_control)
    )
      return `${label}的导出规格无效，请重新选择编码、分辨率与帧率。`;
    if (
      !Number.isInteger(settings.crf) ||
      settings.crf < 0 ||
      settings.crf > 40
    )
      return `${label}的画质值须为 0–40 的整数，数值越小画质越高。`;
    if (
      !Number.isInteger(settings.video_bitrate_kbps) ||
      settings.video_bitrate_kbps < 250 ||
      settings.video_bitrate_kbps > 100000
    )
      return `${label}的视频码率须为 250–100000 kbps 的整数。`;
    if (
      ![64, 96, 128, 160, 192, 256, 320].includes(settings.audio_bitrate_kbps)
    )
      return `${label}的音频码率无效，请重新选择。`;
  }
  return null;
}
