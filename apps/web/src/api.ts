export interface Lyric {
  start_ms: number;
  end_ms: number;
  text: string;
  words: unknown[];
}
export interface Subtitle {
  enabled: boolean;
  layout: "single" | "double" | "ktv";
  font_size: number;
  color: string;
  highlight: string;
  margin_bottom: number;
}
export interface ExportSettings {
  video: boolean;
  audio: boolean;
  video_codec: "h264" | "hevc";
  resolution: "source" | "720p" | "1080p" | "2160p";
  fps: null | 24 | 25 | 30 | 50 | 60;
  rate_control: "quality" | "bitrate";
  crf: number;
  video_bitrate_kbps: number;
  audio_bitrate_kbps: 64 | 96 | 128 | 160 | 192 | 256 | 320;
}
export interface Clip {
  id: string;
  start_ms: number;
  end_ms: number;
  title: string;
  original_artist: string;
  performer: string;
  detection: "manual" | "autoslicer";
  candidates: unknown[];
  lyrics: Lyric[];
  subtitle: Subtitle;
  export_settings: ExportSettings;
}
export interface Project {
  id: string;
  revision: number;
  name: string;
  source_name: string;
  source_file: string;
  duration_ms: number;
  source_fps: number | null;
  has_video: boolean;
  preview_file: string | null;
  clips: Clip[];
}
export interface Job {
  id: string;
  project_id: string;
  kind: string;
  status: "queued" | "running" | "succeeded" | "failed" | "interrupted";
  message: string;
  artifacts: string[];
}
export interface Capabilities {
  detector_ready: boolean;
  ffmpeg: boolean;
  ffprobe: boolean;
  subtitle_burn: boolean;
  recognition: boolean;
  alignment: boolean;
}
export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}
let session: Promise<string> | undefined;
async function token() {
  session ??= fetch("/api/session")
    .then(async (r) => {
      if (!r.ok) throw new Error("无法连接本地服务");
      return (await r.json()).token as string;
    })
    .catch((e) => {
      session = undefined;
      throw e;
    });
  return session;
}
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.method && init.method !== "GET")
    headers.set("X-Studio-Token", await token());
  const response = await fetch(`/api${path}`, { ...init, headers });
  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const body = await response.json();
      message =
        typeof body.detail === "string"
          ? body.detail
          : typeof body.message === "string"
            ? body.message
            : message;
    } catch {
      /* retain status */
    }
    throw new ApiError(message, response.status);
  }
  return response.json() as Promise<T>;
}
const post = <T>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    ...(body === undefined
      ? {}
      : {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }),
  });
export const api = {
  projects: () => request<Project[]>("/projects"),
  project: (id: string) => request<Project>(`/projects/${id}`),
  capabilities: () => request<Capabilities>("/capabilities"),
  jobs: () => request<Job[]>("/jobs"),
  upload: (file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<Project>("/projects/upload", { method: "POST", body });
  },
  save: (project: Project) =>
    request<Project>(`/projects/${project.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        revision: project.revision,
        clips: project.clips,
      }),
    }),
  detect: (id: string) => post<Job>(`/projects/${id}/detect`),
  preview: (id: string) => post<Job>(`/projects/${id}/preview`),
  export: (id: string, clip: string) =>
    post<Job>(`/projects/${id}/clips/${clip}/export`),
  lyrics: (id: string, clip: string, text: string, offset_ms: number) =>
    post<Project>(`/projects/${id}/clips/${clip}/lyrics`, { text, offset_ms }),
  media: (project: Project) =>
    `/api/projects/${project.id}/media?v=${encodeURIComponent(project.preview_file || "source")}`,
  artifact: (job: Job, name: string) =>
    `/api/artifacts/${job.id}/${encodeURIComponent(name)}`,
};
