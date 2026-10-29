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
  mode?: "precise" | "fast";
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
export type DetectorDevice = "auto" | "cpu" | "mps" | "cuda";
export interface ModelInfo {
  id: string;
  name: string;
  filename: string;
  sha256: string;
  size_bytes: number;
  architecture: string;
  classes: number;
  origin: "preconfigured" | "imported" | "trained";
  available: boolean;
  unavailable_reason: string | null;
}
export interface ModelCatalog {
  models: ModelInfo[];
  devices: { id: DetectorDevice; label: string; available: boolean }[];
  engine_ready: boolean;
  engine_error: string | null;
}
export interface TrainingRegion {
  start_ms: number;
  end_ms: number;
  label: "target" | "other";
}
export interface TrainingSample {
  id: string;
  name: string;
  duration_ms: number;
  sha256: string;
  split: "train" | "validation";
  reviewed: boolean;
  regions: TrainingRegion[];
  preview_available?: boolean;
}
export interface TrainingDataset {
  id: string;
  name: string;
  target_name: string;
  revision: number;
  samples: TrainingSample[];
}
export interface TrainingSettings {
  device: DetectorDevice;
  steps: number;
  batch_size: number;
  window_s: number;
  val_every: number;
  lr: number;
  seed: number;
  warmup: number;
}
export interface TrainingJob {
  id: string;
  dataset_id: string;
  dataset_revision: number;
  status:
    | "queued"
    | "running"
    | "succeeded"
    | "failed"
    | "cancelled"
    | "interrupted";
  stage: string;
  message: string;
  progress: number;
  step: number;
  total_steps: number;
  history: { step: number; [key: string]: unknown }[];
  log_tail: string;
  model: ModelInfo | null;
  error: string | null;
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
  preview_profile?: string | null;
  high_preview_file?: string | null;
  source_width?: number | null;
  source_height?: number | null;
  source_browser_compatible?: boolean;
  owner?: string | null;
  shared?: boolean;
  source_project_id?: string | null;
  can_edit?: boolean;
  can_clone?: boolean;
  recording?: Record<string, unknown> | null;
  clips: Clip[];
  detector_model_id: string | null;
  detector_device: DetectorDevice;
}
export interface Job {
  id: string;
  project_id: string;
  kind: string;
  owner?: string | null;
  task?: { profile?: PreviewQuality };
  status:
    | "queued"
    | "running"
    | "succeeded"
    | "failed"
    | "interrupted"
    | "cancelled";
  message: string;
  progress?: number;
  artifacts: string[];
  model_name?: string;
  model_id?: string;
  device?: string;
}
export interface CacheReport {
  categories: { key: string; label: string; bytes: number; files: number }[];
  bytes: number;
  files: number;
  retention_days: number;
}
export interface CacheCleanup extends CacheReport {
  removed_bytes: number;
  removed_files: number;
  failed_files: number;
}
export type PreviewQuality = "low" | "high";
export interface RecorderStatus {
  disk_warning?: string;
  enabled: boolean;
  connected: boolean;
  max_rooms: number;
  queue_limit: number;
  counts: Record<string, number>;
  message: string;
}
export interface RecorderRoom {
  room_id: number;
  name: string;
  title: string;
  auto_record: boolean;
  recording: boolean;
  streaming: boolean;
}
export interface Recording {
  id: string;
  room_id: number;
  session_id: string;
  name: string;
  title: string;
  opened_at: string;
  closed_at: string;
  expected_size: number;
  duration: number;
  status: "queued" | "importing" | "ready" | "failed";
  attempts: number;
  project_id: string | null;
  error: string;
}
export interface Capabilities {
  user?: string;
  role?: "admin" | "editor" | "viewer";
  training_enabled?: boolean;
  low_resource?: boolean;
  export_max_seconds?: number;
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
    if (status === 401 && typeof window !== "undefined")
      window.dispatchEvent(new Event("songcut-auth-required"));
  }
}
const expiredSessionDetail = "会话已失效，请刷新工作台";
interface Session {
  token: Promise<string>;
}
let session: Session | undefined;
let sessionGeneration = 0;
export function resetApiSession() {
  session = undefined;
  sessionGeneration++;
}
function assertSessionGeneration(expected: number) {
  if (expected !== sessionGeneration)
    throw new DOMException(
      "登录状态已变化，请重新读取项目确认结果。",
      "AbortError",
    );
}

function errorMessage(status: number, body: unknown, fallback: string) {
  if (body !== null && typeof body === "object") {
    const { detail, message } = body as { detail?: unknown; message?: unknown };
    if (typeof detail === "string" && detail) return detail;
    if (typeof message === "string" && message) return message;
  }
  return status === 401 ? "登录已失效，请使用服务器账户重新登录。" : fallback;
}
function expiredSession(status: number, body: unknown) {
  return (
    status === 403 &&
    body !== null &&
    typeof body === "object" &&
    (body as { detail?: unknown }).detail === expiredSessionDetail
  );
}
function getSession(stale?: Session): Session {
  // Compare the actual session request, not its token: concurrent rejections of
  // an old token must join the same refresh, including late arriving responses.
  if (stale && session === stale) session = undefined;
  if (!session) {
    const generation = sessionGeneration;
    const next: Session = {
      token: fetch("/api/session", { cache: "no-store" })
        .then(async (response) => {
          const body: unknown = await response.json().catch(() => undefined);
          assertSessionGeneration(generation);
          if (!response.ok)
            throw new ApiError(
              errorMessage(
                response.status,
                body,
                "无法获取工作台会话，请检查本地服务后重试。",
              ),
              response.status,
            );
          const value =
            body && typeof body === "object"
              ? (body as { token?: unknown }).token
              : undefined;
          if (typeof value !== "string" || !value.trim())
            throw new Error("工作台会话响应异常，请检查本地服务后重试。");
          return value;
        })
        .catch((error: unknown) => {
          if (session === next) session = undefined;
          if (error instanceof TypeError)
            throw new Error("无法连接本地服务，请确认服务已启动后重试。");
          throw error;
        }),
    };
    session = next;
  }
  return session;
}
function withAbort<T>(
  promise: Promise<T>,
  signal?: AbortSignal | null,
): Promise<T> {
  if (!signal) return promise;
  return new Promise<T>((resolve, reject) => {
    const cleanup = () => signal.removeEventListener("abort", abort);
    const abort = () => {
      cleanup();
      reject(signal.reason);
    };
    signal.addEventListener("abort", abort, { once: true });
    promise.then(
      (value) => {
        cleanup();
        resolve(value);
      },
      (error: unknown) => {
        cleanup();
        reject(error);
      },
    );
    if (signal.aborted) abort();
  });
}
export async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const generation = sessionGeneration;
  const protectedRequest = !["GET", "HEAD", "OPTIONS"].includes(
    (init.method ?? "GET").toUpperCase(),
  );
  init.signal?.throwIfAborted();
  let currentSession = protectedRequest ? getSession() : undefined;
  for (let attempt = 0; ; attempt++) {
    const headers = new Headers(init.headers);
    if (currentSession)
      headers.set(
        "X-Studio-Token",
        await withAbort(currentSession.token, init.signal),
      );
    init.signal?.throwIfAborted();
    assertSessionGeneration(generation);
    const response = await fetch(`/api${path}`, { ...init, headers });
    assertSessionGeneration(generation);
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      init.signal?.throwIfAborted();
      if (response.ok)
        throw new ApiError(
          "服务器响应异常，请重新载入项目确认操作是否已完成。",
          response.status,
        );
    }
    init.signal?.throwIfAborted();
    assertSessionGeneration(generation);
    // The middleware rejects this exact error before invoking any mutation.
    // Never replay ordinary 403s, network errors, or ambiguous server responses.
    if (
      attempt === 0 &&
      currentSession &&
      expiredSession(response.status, body)
    ) {
      currentSession = getSession(currentSession);
      continue;
    }
    if (!response.ok)
      throw new ApiError(
        errorMessage(response.status, body, `请求失败（${response.status}）`),
        response.status,
      );
    return body as T;
  }
}

export interface UploadProgress {
  phase: "uploading" | "processing";
  loaded: number;
  total: number | null;
}
interface UploadResponse {
  status: number;
  body: unknown;
}
function sendUpload(
  path: string,
  body: FormData,
  sessionToken: string,
  onProgress?: (progress: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<UploadResponse> {
  signal?.throwIfAborted();
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    let settled = false;
    let transferred: UploadProgress = {
      phase: "uploading",
      loaded: 0,
      total: null,
    };
    const abort = () => {
      xhr.abort();
      finish(() => reject(signal?.reason));
    };
    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      signal?.removeEventListener("abort", abort);
      callback();
    };
    xhr.open("POST", `/api${path}`);
    xhr.setRequestHeader("X-Studio-Token", sessionToken);
    xhr.upload.onprogress = (event) => {
      if (settled) return;
      transferred = {
        phase: "uploading",
        loaded: event.loaded,
        total: event.lengthComputable ? event.total : null,
      };
      onProgress?.(transferred);
    };
    xhr.upload.onload = () => {
      if (!settled) onProgress?.({ ...transferred, phase: "processing" });
    };
    xhr.onerror = () =>
      finish(() =>
        reject(
          new Error("上传连接中断，请重新载入项目确认素材是否已导入后再重试。"),
        ),
      );
    xhr.onabort = () =>
      finish(() =>
        reject(
          signal?.aborted
            ? signal.reason
            : new Error("上传已取消，请重新选择文件。"),
        ),
      );
    xhr.ontimeout = () =>
      finish(() =>
        reject(
          new Error("上传超时，请重新载入项目确认素材是否已导入后再重试。"),
        ),
      );
    xhr.onload = () => {
      let result: unknown;
      try {
        result = JSON.parse(xhr.responseText);
      } catch {
        /* retain status */
      }
      finish(() => resolve({ status: xhr.status, body: result }));
    };
    signal?.addEventListener("abort", abort, { once: true });
    try {
      xhr.send(body);
    } catch (error) {
      finish(() => reject(error));
    }
  });
}
// fetch does not expose upload byte progress. Replay only an explicit session
// rejection; network failures may have already imported the file on the server.
export async function uploadForm<T>(
  path: string,
  body: FormData,
  onProgress?: (progress: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<T> {
  const generation = sessionGeneration;
  signal?.throwIfAborted();
  let currentSession = getSession();
  for (let attempt = 0; ; attempt++) {
    const sessionToken = await withAbort(currentSession.token, signal);
    signal?.throwIfAborted();
    assertSessionGeneration(generation);
    const response = await sendUpload(
      path,
      body,
      sessionToken,
      onProgress,
      signal,
    );
    signal?.throwIfAborted();
    assertSessionGeneration(generation);
    if (attempt === 0 && expiredSession(response.status, response.body)) {
      currentSession = getSession(currentSession);
      onProgress?.({ phase: "uploading", loaded: 0, total: null });
      continue;
    }
    if (response.status < 200 || response.status >= 300) {
      const fallback =
        response.status === 413
          ? "文件超过服务器允许的上传大小，请调整服务器限制或选择较小的文件。"
          : `导入失败（${response.status}），请检查素材并重试。`;
      throw new ApiError(
        errorMessage(response.status, response.body, fallback),
        response.status,
      );
    }
    if (
      response.body === null ||
      typeof response.body !== "object" ||
      Array.isArray(response.body) ||
      !Object.keys(response.body).length
    )
      throw new Error("服务器响应异常，请重新载入项目确认素材是否已导入。");
    return response.body as T;
  }
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
export function projectEdits(project: Project) {
  return {
    clips: project.clips,
    detector_model_id: project.detector_model_id ?? null,
    detector_device: project.detector_device ?? "auto",
  };
}
export const api = {
  recorderStatus: (signal?: AbortSignal) =>
    request<RecorderStatus>("/recorder/status", { signal }),
  recorderRooms: (signal?: AbortSignal) =>
    request<RecorderRoom[]>("/recorder/rooms", { signal }),
  recordings: (signal?: AbortSignal) =>
    request<Recording[]>("/recordings", { signal }),
  addRecorderRoom: (room_id: number) =>
    post<RecorderRoom>("/recorder/rooms", { room_id, auto_record: true }),
  configureRecorderRoom: (room_id: number, auto_record: boolean) =>
    request<RecorderRoom>(`/recorder/rooms/${room_id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ auto_record }),
    }),
  recorderAction: (room_id: number, action: "start" | "stop") =>
    post<RecorderRoom>(`/recorder/rooms/${room_id}/${action}`),
  retryRecording: (id: string) => post(`/recordings/${id}/retry`),
  clone: (id: string) => post<Project>(`/projects/${id}/clone`),
  cancelJob: (id: string) => post<Job>(`/jobs/${id}/cancel`),
  cache: (signal?: AbortSignal) => request<CacheReport>("/cache", { signal }),
  cleanCache: () => request<CacheCleanup>("/cache/clean", { method: "POST" }),
  trainingDatasets: () => request<TrainingDataset[]>("/training/datasets"),
  trainingDataset: (id: string) =>
    request<TrainingDataset>(`/training/datasets/${id}`),
  createTrainingDataset: (name: string, target_name: string) =>
    post<TrainingDataset>("/training/datasets", { name, target_name }),
  uploadTrainingSample: (
    dataset: TrainingDataset,
    file: File,
    onProgress?: (progress: UploadProgress) => void,
    signal?: AbortSignal,
  ) => {
    const body = new FormData();
    body.append("file", file);
    body.append("revision", String(dataset.revision));
    return uploadForm<TrainingDataset>(
      `/training/datasets/${dataset.id}/samples`,
      body,
      onProgress,
      signal,
    );
  },
  saveTrainingSample: (dataset: TrainingDataset, sample: TrainingSample) =>
    request<TrainingDataset>(
      `/training/datasets/${dataset.id}/samples/${sample.id}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          revision: dataset.revision,
          split: sample.split,
          reviewed: sample.reviewed,
          regions: sample.regions,
        }),
      },
    ),
  deleteTrainingSample: (dataset: TrainingDataset, sample: TrainingSample) =>
    request<TrainingDataset>(
      `/training/datasets/${dataset.id}/samples/${sample.id}`,
      {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ revision: dataset.revision }),
      },
    ),
  trainingMedia: (dataset: string, sample: string) =>
    `/api/training/datasets/${dataset}/samples/${sample}/media`,
  trainingPreview: (dataset: string, sample: string) =>
    post<Job>(`/training/datasets/${dataset}/samples/${sample}/preview`),
  startTraining: (dataset: TrainingDataset, settings: TrainingSettings) =>
    post<TrainingJob>(`/training/datasets/${dataset.id}/train`, {
      revision: dataset.revision,
      settings,
    }),
  trainingJobs: (signal?: AbortSignal) =>
    request<TrainingJob[]>("/training/jobs", { signal }),
  cancelTraining: (id: string) =>
    post<TrainingJob>(`/training/jobs/${id}/cancel`),
  models: () => request<ModelCatalog>("/models"),
  uploadModel: (file: File, name?: string) => {
    const body = new FormData();
    body.append("file", file);
    if (name?.trim()) body.append("name", name.trim());
    return request<ModelInfo>("/models/upload", { method: "POST", body });
  },
  projects: () => request<Project[]>("/projects"),
  project: (id: string, signal?: AbortSignal) =>
    request<Project>(`/projects/${id}`, { signal }),
  capabilities: () => request<Capabilities>("/capabilities"),
  jobs: (projectId?: string, signal?: AbortSignal) =>
    request<Job[]>(
      `/jobs${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ""}`,
      { signal },
    ),
  upload: (
    file: File,
    onProgress?: (progress: UploadProgress) => void,
    signal?: AbortSignal,
  ) => {
    const body = new FormData();
    body.append("file", file);
    return uploadForm<Project>("/projects/upload", body, onProgress, signal);
  },
  save: (project: Project, signal?: AbortSignal) =>
    request<Project>(`/projects/${project.id}`, {
      method: "PUT",
      signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        revision: project.revision,
        ...projectEdits(project),
      }),
    }),
  detect: (id: string) => post<Job>(`/projects/${id}/detect`),
  preview: (id: string, profile: PreviewQuality = "low") =>
    post<Job>(`/projects/${id}/preview`, { profile }),
  export: (id: string, clip: string) =>
    post<Job>(`/projects/${id}/clips/${clip}/export`),
  lyrics: (id: string, clip: string, text: string, offset_ms: number) =>
    post<Project>(`/projects/${id}/clips/${clip}/lyrics`, { text, offset_ms }),
  media: (project: Project, quality: PreviewQuality = "low") =>
    `/api/projects/${project.id}/media?quality=${quality}&v=${encodeURIComponent(((quality === "high" ? project.high_preview_file : project.preview_file) || "source").split(/[\\/]/).pop() || "source")}`,
  artifact: (job: Job, name: string) =>
    `/api/artifacts/${job.id}/${encodeURIComponent(name)}`,
};
