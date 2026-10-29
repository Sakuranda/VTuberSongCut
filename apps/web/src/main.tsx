import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { api, ApiError, projectEdits } from "./api";
import type {
  Capabilities,
  Clip,
  Job,
  ModelCatalog,
  ModelInfo,
  Project,
} from "./api";
import {
  DEFAULT_EXPORT_SETTINGS,
  editingFps,
  formatTimecode as format,
  normalizeProject,
  stepFrames,
  validateProject,
} from "./editing";
import { Timeline } from "./Timeline";
import { TimecodeInput } from "./TimecodeInput";
import { ExportControls } from "./ExportControls";
import { ModelSelector, ModelManager } from "./ModelControls";
import { RecordingsWorkspace } from "./RecordingsWorkspace";
import { previewReady, mergePreviewMetadata } from "./playback";
import type { PreviewQuality } from "./api";
import { TrainingWorkspace } from "./TrainingWorkspace";
import { UploadFeedback } from "./UploadFeedback";
import type { FileUploadState } from "./UploadFeedback";
import { AuthGate } from "./auth/AuthGate";
import { unsavedChanges } from "./auth/UnsavedChanges";
import { OperationProgress } from "./OperationProgress";
import { usePolling } from "./usePolling";
import { CacheSettings } from "./CacheSettings";
import { AppearanceSettings } from "./AppearanceSettings";
import { useAppearance } from "./theme";
import { SkinWelcome, SkinAvatar } from "./SkinWelcome";
import "./styles.css";
import "./training.css";
import "./SkinWelcome.css";
import "./themes.css";

const length = (ms: number) =>
  `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;
const jobLabels: Record<string, string> = {
  detect: "检测演唱片段",
  detection: "检测演唱片段",
  preview: "生成兼容预览",
  export: "导出歌曲",
};
const statuses: Record<Job["status"], string> = {
  queued: "等待中",
  running: "处理中",
  succeeded: "已完成",
  failed: "失败",
  interrupted: "已中断",
  cancelled: "已取消",
};
function Icon({
  name,
  size = 20,
}: {
  name:
    | "music"
    | "upload"
    | "play"
    | "plus"
    | "cut"
    | "download"
    | "settings"
    | "wave"
    | "folder";
  size?: number;
}) {
  const paths = {
    music: (
      <>
        <path d="M9 18V5l11-2v13M9 8l11-2" />
        <ellipse cx="6" cy="18" rx="3" ry="2" />
        <ellipse cx="17" cy="16" rx="3" ry="2" />
      </>
    ),
    upload: (
      <>
        <path d="M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5" />
      </>
    ),
    play: <path d="m9 5 11 7-11 7Z" />,
    plus: <path d="M12 5v14M5 12h14" />,
    cut: (
      <>
        <circle cx="5" cy="6" r="3" />
        <circle cx="5" cy="18" r="3" />
        <path d="m8 8 13 12M8 16 21 3" />
      </>
    ),
    download: <path d="M12 3v13m-5-5 5 5 5-5M4 16v5h16v-5" />,
    settings: (
      <>
        <path d="M4 7h16M4 17h16" />
        <circle cx="9" cy="7" r="3" />
        <circle cx="15" cy="17" r="3" />
      </>
    ),
    wave: <path d="M3 10v4m4-7v10m5-15v20m5-15v10m4-7v4" />,
    folder: <path d="M3 5h7l2 3h9v12H3Z" />,
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.65"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name]}
    </svg>
  );
}
function App({
  sessionActive = true,
  onLogout,
}: {
  sessionActive?: boolean;
  onLogout?: () => void;
}) {
  const { appearance, resolvedMode, updateAppearance, resetAppearance } =
    useAppearance();
  const [appearanceOpen, setAppearanceOpen] = useState(false);
  const [cacheOpen, setCacheOpen] = useState(false);
  const [workspace, setWorkspace] = useState<"cut" | "training" | "recordings">(
    "cut",
  );
  const [catalog, setCatalog] = useState<ModelCatalog | null>(null);
  const [catalogError, setCatalogError] = useState("");
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [saved, setSaved] = useState("");
  const [selected, setSelected] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [busy, setBusy] = useState("");
  const [uploadState, setUploadState] = useState<FileUploadState | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [conflict, setConflict] = useState(false);
  const [refreshPending, setRefreshPending] = useState(false);
  const [now, setNow] = useState(0);
  const [mediaError, setMediaError] = useState(false);
  const [playbackChoice, setPlaybackChoice] = useState<{
    id: string;
    quality: PreviewQuality;
  }>({ id: "", quality: "low" });
  const quality: PreviewQuality =
    playbackChoice.id === project?.id ? playbackChoice.quality : "low";
  const ready = !!project && previewReady(project, quality);
  const [previewFailure, setPreviewFailure] = useState<{
    key: string;
    message: string;
  } | null>(null);
  const previewRequests = useRef(new Set<string>());
  const [mediaEpoch, setMediaEpoch] = useState(0);
  const playbackPosition = useRef({ id: "", time: 0 });
  const canPrepare = sessionActive && !!caps;
  const canCreate = sessionActive && !!caps && caps.role !== "viewer";
  const canManage = !!caps && (!caps.role || caps.role === "admin");
  const canTrain = canManage && caps?.training_enabled !== false;
  const canEdit =
    sessionActive &&
    !!project &&
    caps?.role !== "viewer" &&
    project.can_edit !== false;
  const [lrc, setLrc] = useState("");
  const [offset, setOffset] = useState(0);
  const [tab, setTab] = useState<"details" | "lyrics" | "export">("details");
  const [loading, setLoading] = useState(true);
  const [startupAttempt, setStartupAttempt] = useState(0);
  const [startupError, setStartupError] = useState("");
  const [capabilityError, setCapabilityError] = useState("");
  const [capabilitiesLoading, setCapabilitiesLoading] = useState(true);
  const [invalidTimes, setInvalidTimes] = useState<Record<string, boolean>>({});
  const invalidTime = Object.values(invalidTimes).some(Boolean);
  const media = useRef<HTMLVideoElement | null>(null);
  const upload = useRef<HTMLInputElement | null>(null);
  const pendingModelId = useRef<string | null>(null);
  const dirty = !!project && JSON.stringify(projectEdits(project)) !== saved;
  useEffect(() => {
    unsavedChanges.set("cut", dirty);
    return () => unsavedChanges.set("cut", false);
  }, [dirty]);
  useEffect(() => {
    if (!sessionActive) media.current?.pause();
  }, [sessionActive]);
  const live = useRef({ project, dirty, busy });
  live.current = { project, dirty, busy };
  const previousJobs = useRef<Job[]>([]);
  const loadGeneration = useRef(0);
  const clip = project?.clips.find((c) => c.id === selected);
  const activeJobs = jobs.filter((j) => j.project_id === project?.id);
  const previewJob = activeJobs.find(
    (job) =>
      job.kind === "preview" && ["queued", "running"].includes(job.status),
  );
  const processing = activeJobs.some(
    (j) => j.status === "running" || j.status === "queued",
  );
  const blocked =
    !canEdit || !!busy || dirty || conflict || processing || invalidTime;
  const detectorModel = catalog?.models.find(
    (model) => model.id === project?.detector_model_id,
  );
  const detectorDevice = catalog?.devices.find(
    (device) => device.id === project?.detector_device,
  );
  const detectorReady =
    !!catalog?.engine_ready &&
    !!detectorModel?.available &&
    !!detectorDevice?.available;
  function switchWorkspace(next: "cut" | "training" | "recordings") {
    media.current?.pause();
    setWorkspace(next);
    window.scrollTo({ top: 0 });
  }
  function openModelLibrary() {
    switchWorkspace("training");
    requestAnimationFrame(() => {
      const library = document.getElementById("model-library");
      library?.focus();
      library?.scrollIntoView({ block: "start" });
    });
  }
  async function refreshCatalog() {
    try {
      setCatalog(await api.models());
      setCatalogError("");
    } catch (e) {
      setCatalogError(e instanceof Error ? e.message : "无法读取模型库");
    }
  }
  async function useModel(model: ModelInfo) {
    if (project && !canEdit) {
      setError("请先创建自己的剪辑，再选择模型。");
      return;
    }
    await refreshCatalog();
    switchWorkspace("cut");
    pendingModelId.current = live.current.project ? null : model.id;
    setProject((current) =>
      current ? { ...current, detector_model_id: model.id } : current,
    );
    setNotice(
      live.current.project
        ? `已选择「${model.name}」，保存修改后可开始检测。`
        : `已准备使用「${model.name}」。导入录播后会自动选中它，再保存即可检测。`,
    );
  }
  useEffect(() => {
    const refreshVisibleCatalog = () => {
      if (sessionActive && !document.hidden) void refreshCatalog();
    };
    refreshVisibleCatalog();
    document.addEventListener("visibilitychange", refreshVisibleCatalog);
    return () =>
      document.removeEventListener("visibilitychange", refreshVisibleCatalog);
  }, [workspace, sessionActive]);
  const fps = editingFps(project?.source_fps);
  const savedOutput = clip?.export_settings || DEFAULT_EXPORT_SETTINGS;
  const output =
    project && !project.has_video
      ? { ...savedOutput, video: false }
      : savedOutput;
  const exportIssue =
    clip && caps && project
      ? caps.export_max_seconds &&
        clip.end_ms - clip.start_ms > caps.export_max_seconds * 1000
        ? `单次导出最长 ${Math.floor(caps.export_max_seconds / 60)} 分钟，请缩短片段。`
        : output.video && output.mode === "fast" && clip.subtitle.enabled
          ? "快速导出不能烧录字幕，请关闭字幕或选择精确导出。"
          : caps.low_resource &&
              output.video &&
              (output.video_codec !== "h264" ||
                output.resolution === "2160p" ||
                (output.resolution === "source" &&
                  (project.source_height || 0) > 1080) ||
                (output.fps ?? project.source_fps ?? 0) > 30)
            ? "请在导出设置中选择最高1080p、30fps、H.264。"
            : ""
      : "";
  const outputLabel =
    [output.video && "MP4", output.audio && "MP3"]
      .filter(Boolean)
      .join(" + ") || "当前片段";
  function accept(raw: Project, preserveSelection = true) {
    const p = normalizeProject(raw);
    setProject(p);
    setSaved(JSON.stringify(projectEdits(p)));
    setSelected((s) =>
      preserveSelection && p.clips.some((c) => c.id === s)
        ? s
        : p.clips[0]?.id || "",
    );
    setConflict(false);
    setRefreshPending(false);
    setProjects((list) => [p, ...list.filter((x) => x.id !== p.id)]);
  }
  async function load(id: string, errorInCaller = false): Promise<boolean> {
    if (
      live.current.dirty &&
      !window.confirm("当前修改尚未保存。放弃修改并打开项目？")
    )
      return false;
    const generation = ++loadGeneration.current;
    setBusy("打开项目");
    setError("");
    try {
      const fresh = await api.project(id);
      if (generation !== loadGeneration.current) return false;
      accept(fresh, false);
      setPlaybackChoice({ id, quality: "low" });
      playbackPosition.current = { id, time: 0 };
      setMediaError(false);
      setNow(0);
      return true;
    } catch (e) {
      if (generation !== loadGeneration.current) return false;
      if (errorInCaller) throw e;
      fail(e);
      return false;
    } finally {
      if (generation === loadGeneration.current) setBusy("");
    }
  }
  function fail(e: unknown) {
    setError(e instanceof Error ? e.message : "操作未完成，请重试");
  }
  async function refreshPreview(id: string, signal?: AbortSignal) {
    const fresh = await api.project(id, signal);
    if (!signal?.aborted && live.current.project?.id === id) {
      setProject((current) =>
        current?.id === id ? mergePreviewMetadata(current, fresh) : current,
      );
      setMediaError(false);
      setMediaEpoch((value) => value + 1);
    }
  }
  async function requestPreview(
    p: Project,
    profile: PreviewQuality,
    retry = false,
  ) {
    const key = `${p.id}:${profile}`;
    if (!canPrepare || (previewRequests.current.has(key) && !retry)) return;
    previewRequests.current.add(key);
    setPreviewFailure(null);
    try {
      const job = await api.preview(p.id, profile);
      if (live.current.project?.id !== p.id) return;
      setJobs((items) => [job, ...items.filter((item) => item.id !== job.id)]);
      previousJobs.current = [
        job,
        ...previousJobs.current.filter((item) => item.id !== job.id),
      ];
      if (job.status === "succeeded") await refreshPreview(p.id);
    } catch (error) {
      if (live.current.project?.id === p.id)
        setPreviewFailure({
          key,
          message:
            error instanceof Error ? error.message : "预览准备失败，请重试",
        });
    }
  }
  useEffect(() => {
    if (workspace === "cut" && project && !ready && canPrepare)
      void requestPreview(project, quality);
  }, [workspace, project?.id, quality, ready, canPrepare]);
  async function cloneProject() {
    if (!project || !canCreate || busy) return;
    if (dirty && !window.confirm("当前修改尚未保存。放弃修改并创建剪辑副本？"))
      return;
    setBusy("创建剪辑");
    setError("");
    try {
      accept(await api.clone(project.id), false);
      setNotice("已创建你的剪辑副本，可以独立编辑。");
    } catch (error) {
      fail(error);
    } finally {
      setBusy("");
    }
  }
  async function cancelJob(job: Job) {
    try {
      const result = await api.cancelJob(job.id);
      setJobs((items) =>
        items.map((item) => (item.id === result.id ? result : item)),
      );
    } catch (error) {
      fail(error);
    }
  }
  useEffect(() => {
    let stopped = false;
    setLoading(true);
    setCapabilitiesLoading(true);
    setStartupError("");
    setCapabilityError("");
    api
      .projects()
      .then((list) => {
        if (stopped) return;
        const current = live.current.project;
        setProjects(
          current && !list.some((item) => item.id === current.id)
            ? [current, ...list]
            : list,
        );
        if (list.length && !live.current.project && !live.current.busy)
          accept(list[0], false);
      })
      .catch((e) => {
        if (!stopped)
          setStartupError(e instanceof Error ? e.message : "项目列表加载失败");
      })
      .finally(() => {
        if (!stopped) setLoading(false);
      });
    // An unavailable media tool must not hide otherwise readable projects.
    api
      .capabilities()
      .then((capabilities) => {
        if (!stopped) setCaps(capabilities);
      })
      .catch((e) => {
        if (!stopped)
          setCapabilityError(
            e instanceof Error ? e.message : "媒体工具检查失败",
          );
      })
      .finally(() => {
        if (!stopped) setCapabilitiesLoading(false);
      });
    return () => {
      stopped = true;
    };
  }, [startupAttempt]);
  usePolling(
    async (signal) => {
      try {
        const items = await api.jobs(live.current.project?.id, signal);
        if (signal.aborted) return;
        const p = live.current.project;
        const changed =
          p &&
          items.some(
            (j) =>
              j.project_id === p.id &&
              j.status === "succeeded" &&
              previousJobs.current.some(
                (old) => old.id === j.id && old.status !== "succeeded",
              ),
          );
        const completedPreviews =
          p &&
          items.some(
            (j) =>
              j.project_id === p.id &&
              j.kind === "preview" &&
              j.status === "succeeded",
          );
        if (
          p &&
          (changed || (!previewReady(p, quality) && completedPreviews))
        ) {
          const fresh = await api.project(p.id, signal);
          if (!signal.aborted && live.current.project?.id === fresh.id) {
            const changedEdits = items.some(
              (j) =>
                j.project_id === p.id &&
                j.kind !== "preview" &&
                j.status === "succeeded" &&
                previousJobs.current.some(
                  (old) => old.id === j.id && old.status !== "succeeded",
                ),
            );
            if (live.current.dirty || live.current.busy) {
              setProject((current) =>
                current?.id === fresh.id
                  ? mergePreviewMetadata(current, fresh)
                  : current,
              );
              if (changedEdits) setRefreshPending(true);
            } else accept(fresh);
            if (
              items.some(
                (j) =>
                  j.kind === "preview" &&
                  j.status === "succeeded" &&
                  previousJobs.current.some(
                    (old) => old.id === j.id && old.status !== "succeeded",
                  ),
              )
            ) {
              setMediaError(false);
              setMediaEpoch((value) => value + 1);
            }
          }
        }
        // Publish the new job status after refreshing the project. Changing the
        // active/idle polling interval earlier would abort this same refresh.
        if (!signal.aborted) {
          previousJobs.current = items;
          setJobs(items);
        }
      } catch {
        /* Keep editing available during a temporary polling failure. */
      }
    },
    sessionActive && workspace === "cut" && !!project,
    processing ? 2000 : 10000,
    project?.id,
  );
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (live.current.dirty || live.current.busy.includes("导入"))
        event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);
  useEffect(() => {
    setLrc("");
    setOffset(0);
    setInvalidTimes({});
  }, [selected, project?.id]);
  function updateClip(values: Partial<Clip>) {
    if (!canEdit || !project || !clip) return;
    setProject({
      ...project,
      clips: project.clips.map((c) =>
        c.id === clip.id ? { ...c, ...values } : c,
      ),
    });
    setNotice("");
  }
  function seek(ms: number) {
    if (!project || !Number.isFinite(ms)) return;
    const bounded = Math.max(0, Math.min(project.duration_ms, Math.round(ms)));
    setNow(bounded);
    playbackPosition.current = { id: project.id, time: bounded / 1000 };
    if (media.current) media.current.currentTime = bounded / 1000;
  }
  function boundary(edge: "start_ms" | "end_ms", ms: number) {
    if (!canEdit || !clip || !project || !Number.isFinite(ms)) return;
    updateClip({
      [edge]: Math.round(
        Math.max(
          edge === "end_ms" ? clip.start_ms + 1 : 0,
          Math.min(
            edge === "start_ms" ? clip.end_ms - 1 : project.duration_ms,
            ms,
          ),
        ),
      ),
    });
  }
  useEffect(() => {
    function shortcuts(event: globalThis.KeyboardEvent) {
      const target = event.target instanceof Element ? event.target : null;
      if (
        !sessionActive ||
        workspace !== "cut" ||
        !project ||
        event.defaultPrevented ||
        event.ctrlKey ||
        event.altKey ||
        event.metaKey ||
        target?.closest(
          'input, textarea, select, button, a, summary, [role="textbox"], [contenteditable="true"]',
        ) ||
        document.querySelector(
          'dialog[open], [aria-modal="true"], [role="dialog"]',
        )
      )
        return;
      const key = event.key.toLowerCase();
      if (key === "i" || key === "o") {
        if (!canEdit || !clip || busy || invalidTime) return;
        event.preventDefault();
        boundary(
          key === "i" ? "start_ms" : "end_ms",
          media.current ? media.current.currentTime * 1000 : now,
        );
      } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
        event.preventDefault();
        media.current?.pause();
        seek(
          stepFrames(
            media.current ? media.current.currentTime * 1000 : now,
            event.key === "ArrowLeft" ? -1 : 1,
            event.shiftKey ? 10 : 1,
            fps,
          ),
        );
      } else if (event.code === "Space" || event.key === " ") {
        if (!media.current || mediaError) return;
        event.preventDefault();
        if (media.current.paused)
          void media.current
            .play()
            .catch(() => setNotice("请点击预览画面中的播放按钮。"));
        else media.current.pause();
      }
    }
    window.addEventListener("keydown", shortcuts);
    return () => window.removeEventListener("keydown", shortcuts);
  }, [
    workspace,
    sessionActive,
    project,
    clip,
    now,
    busy,
    invalidTime,
    fps,
    mediaError,
    canEdit,
  ]);
  async function save() {
    if (!project || !canEdit) return;
    if (invalidTime) {
      setError("请先修正入点或出点的时间戳。");
      return;
    }
    const validationError = validateProject(project);
    if (validationError) {
      setError(validationError);
      return;
    }
    setBusy("保存");
    setError("");
    try {
      accept(await api.save(project));
      setNotice("修改已保存");
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setConflict(true);
        setError(
          "项目已被其他窗口更新。你的修改仍保留，请重新载入最新版本后再编辑。",
        );
      } else fail(e);
    } finally {
      setBusy("");
    }
  }
  async function importVideo(file?: File) {
    if (!file || !canCreate) return;
    if (dirty && !window.confirm("当前修改尚未保存。放弃修改并导入视频？"))
      return;
    setUploadState({
      phase: "uploading",
      filename: file.name,
      loaded: 0,
      total: file.size,
    });
    setBusy("正在导入视频，请稍候");
    setError("");
    try {
      accept(
        await api.upload(file, (progress) =>
          setUploadState({ ...progress, filename: file.name }),
        ),
        false,
      );
      setUploadState({
        phase: "succeeded",
        filename: file.name,
        loaded: file.size,
        total: file.size,
      });
      const pending = pendingModelId.current;
      if (pending) {
        setProject((current) =>
          current ? { ...current, detector_model_id: pending } : current,
        );
        pendingModelId.current = null;
      }
      setMediaError(false);
      setNow(0);
      setNotice(
        pending
          ? "录播已导入，并选中刚才的模型。保存修改后可开始检测。"
          : "录播已导入，可选择模型检测，也可手动添加片段。",
      );
    } catch (e) {
      setUploadState(
        (state) =>
          state && {
            ...state,
            phase: "failed",
            error: e instanceof Error ? e.message : "导入失败，请重试",
          },
      );
    } finally {
      setBusy("");
      if (upload.current) upload.current.value = "";
    }
  }
  async function run(kind: "detect" | "preview" | "export") {
    if (
      !project ||
      blocked ||
      (kind === "export" && !clip) ||
      (kind === "detect" && !detectorReady)
    )
      return;
    setBusy(kind);
    setError("");
    try {
      const job =
        kind === "export"
          ? await api.export(project.id, clip!.id)
          : await api[kind](project.id);
      setJobs((list) => [job, ...list.filter((j) => j.id !== job.id)]);
      previousJobs.current = [
        ...previousJobs.current.filter((j) => j.id !== job.id),
        job,
      ];
      if (job.status === "succeeded") accept(await api.project(project.id));
      setNotice("任务已加入队列，可在下方查看进度");
    } catch (e) {
      fail(e);
    } finally {
      setBusy("");
    }
  }
  function addClip() {
    if (!project || !canEdit) return;
    const start = Math.min(
      Math.round(now),
      Math.max(0, project.duration_ms - 1),
    );
    const item: Clip = {
      id: crypto.randomUUID().replaceAll("-", ""),
      start_ms: start,
      end_ms: Math.min(project.duration_ms, start + 60000),
      title: "",
      original_artist: "",
      performer: "",
      detection: "manual",
      candidates: [],
      lyrics: [],
      export_settings: {
        ...DEFAULT_EXPORT_SETTINGS,
        video: project.has_video,
        ...(caps?.low_resource
          ? {
              resolution:
                (project.source_height || 0) > 1080
                  ? ("1080p" as const)
                  : ("source" as const),
              fps: (project.source_fps || 0) > 30 ? (30 as const) : null,
            }
          : {}),
      },
      subtitle: {
        enabled: false,
        layout: "single",
        font_size: 42,
        color: "#FFFFFF",
        highlight: "#FFCF70",
        margin_bottom: 48,
      },
    };
    setProject({ ...project, clips: [...project.clips, item] });
    setSelected(item.id);
  }
  async function importLyrics() {
    if (!project || !clip || blocked || !lrc.trim()) return;
    setBusy("导入歌词");
    setError("");
    try {
      accept(
        await api.lyrics(project.id, clip.id, lrc, Math.round(offset * 1000)),
      );
      setLrc("");
      setNotice("歌词已导入");
    } catch (e) {
      fail(e);
    } finally {
      setBusy("");
    }
  }
  const lyric = clip?.lyrics.find((l) => now >= l.start_ms && now < l.end_ms);
  return (
    <div className="app-shell">
      <div className="app-body">
        <header className="topbar">
          <div className="brand theme-brand">
            {appearance.skin === "catgirl" && <SkinAvatar />}
            <div>
              <h1>
                拾歌<span>VTuber SongCut</span>
              </h1>
              <p>直播歌切工作台</p>
            </div>
          </div>
          <div className="header-controls">
            {onLogout && (
              <button className="button" onClick={onLogout}>
                退出登录
              </button>
            )}
            <div
              className="top-actions"
              hidden={workspace !== "cut" || !project}
            >
              <span className="local-label">工作空间</span>
              <button
                className="button primary"
                disabled={!!busy || processing || !canCreate}
                onClick={() => upload.current?.click()}
              >
                <Icon name="upload" size={17} />
                {busy.includes("导入视频") ? "正在导入…" : "导入录播"}
              </button>
              <input
                ref={upload}
                hidden
                type="file"
                accept=".mp4,.flv,.mkv,.mov,.webm,.avi,.ts,.m4v,.mp3,.wav,.m4a,.flac,.aac,.ogg,.opus,video/*,audio/*"
                onChange={(e) => void importVideo(e.target.files?.[0])}
              />
            </div>
            <button
              className="button appearance-toggle"
              aria-haspopup="dialog"
              aria-expanded={appearanceOpen}
              onClick={() => setAppearanceOpen(true)}
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.6"
                aria-hidden="true"
              >
                <path d="M12 3a9 9 0 1 0 0 18h1a2 2 0 0 0 1.4-3.4 1.4 1.4 0 0 1 1-2.4h1A4.6 4.6 0 0 0 21 10.6 8 8 0 0 0 12 3Z" />
                <circle cx="7.5" cy="10" r=".9" />
                <circle cx="11" cy="7" r=".9" />
                <circle cx="15.5" cy="8" r=".9" />
              </svg>
              外观
            </button>
          </div>
        </header>
        <CacheSettings open={cacheOpen} onClose={() => setCacheOpen(false)} />
        <AppearanceSettings
          open={appearanceOpen}
          onClose={() => setAppearanceOpen(false)}
          appearance={appearance}
          resolvedMode={resolvedMode}
          onChange={updateAppearance}
          onReset={resetAppearance}
        />
        <nav className="workspace-navigation" aria-label="工作区">
          <button
            className={workspace === "cut" ? "active" : ""}
            aria-current={workspace === "cut" ? "page" : undefined}
            onClick={() => {
              media.current?.pause();
              switchWorkspace("cut");
            }}
          >
            录播切歌
          </button>
          <button
            className={workspace === "training" ? "active" : ""}
            aria-current={workspace === "training" ? "page" : undefined}
            onClick={() => {
              media.current?.pause();
              switchWorkspace("training");
            }}
          >
            {canTrain ? "模型训练" : "模型管理"}
          </button>
          <button
            className={workspace === "recordings" ? "active" : ""}
            aria-current={workspace === "recordings" ? "page" : undefined}
            onClick={() => switchWorkspace("recordings")}
          >
            录播库
          </button>
        </nav>
        <main>
          <div hidden={workspace !== "cut"} aria-label="录播切歌工作区">
            <div className="page-heading">
              <div>
                <h2>{project ? project.name : "录播切歌"}</h2>
              </div>
              {project && canEdit && (
                <div className="save-group">
                  <span className={dirty ? "unsaved" : "saved"}>
                    {dirty ? "● 有未保存修改" : "✓ 修改已保存"}
                  </span>
                  <button
                    className="button"
                    disabled={!dirty || !!busy || conflict || invalidTime}
                    onClick={() => void save()}
                  >
                    保存修改
                  </button>
                </div>
              )}
            </div>
            {project && !canEdit && (
              <div className="banner info readonly-note">
                <span>正在浏览共享录播，原始内容保持不变。</span>
                {project.can_clone && canCreate && (
                  <button
                    className="button"
                    disabled={!!busy}
                    onClick={() => void cloneProject()}
                  >
                    创建我的剪辑
                  </button>
                )}
              </div>
            )}
            {startupError && (
              <div className="banner error" role="alert">
                <span>项目列表暂时无法读取：{startupError}</span>
                <button
                  disabled={loading}
                  onClick={() => setStartupAttempt((value) => value + 1)}
                >
                  重新连接
                </button>
              </div>
            )}
            {capabilityError && (
              <div className="banner info" role="status">
                <span>媒体工具暂时无法检查，可继续查看与编辑项目。</span>
                <button
                  disabled={capabilitiesLoading}
                  onClick={() => setStartupAttempt((value) => value + 1)}
                >
                  重试检查
                </button>
              </div>
            )}
            {error && (
              <div className="banner error" role="alert">
                <span>{error}</span>
                {conflict && project && (
                  <button onClick={() => void load(project.id)}>
                    放弃本地修改并重新载入
                  </button>
                )}
                <button
                  className="dismiss"
                  onClick={() => setError("")}
                  aria-label="关闭错误提示"
                >
                  ×
                </button>
              </div>
            )}
            {notice && (
              <div className="banner info" role="status">
                {notice}
                <button
                  className="dismiss"
                  onClick={() => setNotice("")}
                  aria-label="关闭提示"
                >
                  ×
                </button>
              </div>
            )}
            {refreshPending && (
              <div className="banner info">
                后台任务已完成。请先保存修改，再重新载入项目以查看结果。
                <button
                  disabled={dirty || !!busy}
                  onClick={() => project && void load(project.id)}
                >
                  重新载入
                </button>
              </div>
            )}
            <UploadFeedback
              state={uploadState}
              onRetry={() => upload.current?.click()}
              disabled={!!busy || processing || !canCreate}
            />
            <div className={`workbench${!project ? " first-use" : ""}`}>
              <section className="panel library">
                <div className="panel-heading">
                  <h3>
                    <Icon name="folder" size={17} /> 直播素材
                  </h3>
                  <span className="count">{projects.length}</span>
                </div>
                <label className="project-select">
                  <span>当前项目</span>
                  <select
                    value={project?.id || ""}
                    disabled={!!busy}
                    onChange={(e) => void load(e.target.value)}
                  >
                    {!project && <option value="">选择项目</option>}
                    {projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </label>
                {project && (
                  <div className="source-summary">
                    <div className="source-symbol">
                      <Icon name="play" size={15} />
                    </div>
                    <div>
                      <strong title={project.source_name}>
                        {project.source_name}
                      </strong>
                      <span>
                        {length(project.duration_ms)} ·{" "}
                        {project.has_video ? "视频素材" : "音频素材"}
                      </span>
                    </div>
                  </div>
                )}
                <ModelSelector
                  trainingEnabled={canTrain}
                  canImport={canManage}
                  onImport={openModelLibrary}
                  project={project}
                  catalog={catalog}
                  disabled={!!busy || processing || !canEdit}
                  onChange={(patch) => {
                    setProject((current) =>
                      current ? { ...current, ...patch } : current,
                    );
                    setNotice("");
                  }}
                  onTrain={() => {
                    media.current?.pause();
                    switchWorkspace("training");
                  }}
                />
                {catalogError && (
                  <p className="model-warning library-hint">
                    {catalogError}{" "}
                    <button
                      className="text-action"
                      onClick={() => void refreshCatalog()}
                    >
                      重新读取模型库
                    </button>
                  </p>
                )}
                <div className="songs-heading">
                  <h3>
                    歌曲片段{" "}
                    <span className="count">{project?.clips.length || 0}</span>
                  </h3>
                  <button
                    className="icon-button"
                    onClick={addClip}
                    disabled={!project || !canEdit || !!busy}
                    aria-label="在播放位置添加片段"
                    title="在播放位置添加片段"
                  >
                    <Icon name="plus" size={18} />
                  </button>
                </div>
                <div className="clip-list">
                  {project?.clips.map((c, i) => (
                    <button
                      className={`clip-item ${c.id === selected ? "selected" : ""}`}
                      key={c.id}
                      onClick={() => {
                        setSelected(c.id);
                        seek(c.start_ms);
                      }}
                    >
                      <span className="clip-number">
                        {String(i + 1).padStart(2, "0")}
                      </span>
                      <div>
                        <strong>{c.title || "未命名歌曲"}</strong>
                        <span>{c.performer || "待填写演唱者"}</span>
                        <code>
                          {format(c.start_ms).slice(0, -4)} —{" "}
                          {format(c.end_ms).slice(0, -4)}
                        </code>
                      </div>
                      <span className="clip-origin">
                        {c.detection === "manual" ? "手动" : "检测"}
                      </span>
                    </button>
                  ))}
                  {!project?.clips.length && (
                    <div className="empty-clips">
                      <Icon name="music" size={30} />
                      <strong>还没有歌曲片段</strong>
                      <p>
                        导入直播后检测演唱片段，
                        <br />
                        或在播放位置手动添加。
                      </p>
                    </div>
                  )}
                </div>
                <div className="detect-area">
                  <button
                    className="button detect-button"
                    disabled={!project || blocked || !detectorReady}
                    onClick={() => void run("detect")}
                  >
                    <Icon name="wave" size={17} />
                    检测演唱片段
                  </button>
                  <p>
                    {!project
                      ? "先导入直播录像，再选择检测模型"
                      : !catalog
                        ? "正在读取模型信息…"
                        : !project.detector_model_id
                          ? "先选择目标主播的模型"
                          : !detectorModel?.available
                            ? "已选模型不可用，请重新选择"
                            : !catalog?.engine_ready
                              ? "模型环境未就绪，可先手动添加片段"
                              : !detectorDevice?.available
                                ? "所选设备不可用，请选择自动或 CPU"
                                : "检测结果追加到列表，保留已有片段"}
                  </p>
                  {dirty && (
                    <p className="save-hint">
                      <button
                        className="text-action"
                        disabled={!!busy || conflict || invalidTime}
                        onClick={() => void save()}
                      >
                        保存修改
                      </button>{" "}
                      后可继续检测与导出
                    </p>
                  )}
                </div>
              </section>
              <div className="center-column">
                <section className="panel preview-panel">
                  <div className="panel-heading">
                    <h3>画面预览</h3>
                    {project?.has_video ? (
                      <label className="preview-quality">
                        清晰度
                        <select
                          aria-label="预览清晰度"
                          value={quality}
                          onChange={(event) => {
                            playbackPosition.current = {
                              id: project.id,
                              time: media.current?.currentTime ?? now / 1000,
                            };
                            setPlaybackChoice({
                              id: project.id,
                              quality: event.target.value as PreviewQuality,
                            });
                            setMediaError(false);
                          }}
                        >
                          <option value="low">省流 320p</option>
                          <option value="high">高清（最高1080p）</option>
                        </select>
                      </label>
                    ) : (
                      <span className="small muted">
                        {project ? "原始素材时间" : "等待导入素材"}
                      </span>
                    )}
                  </div>
                  <div
                    className={`video-stage ${!project ? "empty-stage" : ""}`}
                  >
                    {project ? (
                      <>
                        {ready ? (
                          <video
                            key={`${project.id}-${quality}-${quality === "high" ? project.high_preview_file : project.preview_file}-${mediaEpoch}`}
                            ref={media}
                            src={api.media(project, quality)}
                            controls
                            preload="metadata"
                            onLoadedMetadata={(event) => {
                              const position = playbackPosition.current;
                              const time =
                                position.id === project.id ? position.time : 0;
                              event.currentTarget.currentTime = Math.min(
                                time,
                                event.currentTarget.duration || time,
                              );
                              setNow(Math.round(time * 1000));
                            }}
                            onTimeUpdate={(e) => {
                              if (e.currentTarget.readyState < 1) return;
                              playbackPosition.current = {
                                id: project.id,
                                time: e.currentTarget.currentTime,
                              };
                              setNow(
                                Math.round(e.currentTarget.currentTime * 1000),
                              );
                            }}
                            onError={() => setMediaError(true)}
                            onLoadedData={() => setMediaError(false)}
                          />
                        ) : (
                          <div className="preview-waiting">
                            <strong>
                              {quality === "low"
                                ? previewJob
                                  ? "正在准备省流预览"
                                  : "省流预览尚未就绪"
                                : "高清预览尚未准备好"}
                            </strong>
                            <p>
                              {previewFailure?.key ===
                              `${project.id}:${quality}`
                                ? previewFailure.message
                                : previewJob?.message ||
                                  activeJobs.find(
                                    (job) =>
                                      job.kind === "preview" &&
                                      [
                                        "failed",
                                        "interrupted",
                                        "cancelled",
                                      ].includes(job.status),
                                  )?.message ||
                                  (canPrepare
                                    ? "预览准备好后会自动显示。"
                                    : "正在读取工作空间，请稍候。")}
                            </p>
                            {canPrepare && !previewJob && (
                              <button
                                className="button"
                                onClick={() =>
                                  void requestPreview(project, quality, true)
                                }
                              >
                                重新准备预览
                              </button>
                            )}
                          </div>
                        )}
                        {!project.has_video && (
                          <div className="audio-note">
                            <Icon name="music" size={42} />
                            <span>音频素材</span>
                          </div>
                        )}
                        {clip?.subtitle.enabled && lyric && (
                          <div
                            className={`lyric-preview ${clip.subtitle.layout}`}
                            style={{
                              color: clip.subtitle.color,
                              bottom: `${Math.min(30, clip.subtitle.margin_bottom / 5)}%`,
                              fontSize: `${Math.min(30, clip.subtitle.font_size / 2)}px`,
                            }}
                          >
                            {lyric.text}
                          </div>
                        )}
                        {ready && mediaError && (
                          <div className="media-error">
                            <strong>浏览器暂时无法播放此素材</strong>
                            <p>生成兼容预览后继续剪辑，原始素材会用于导出。</p>
                            <button
                              className="button primary"
                              disabled={
                                !canPrepare || !!previewJob || !caps?.ffmpeg
                              }
                              onClick={() =>
                                void requestPreview(project, quality, true)
                              }
                            >
                              生成兼容预览
                            </button>
                          </div>
                        )}
                      </>
                    ) : (
                      <SkinWelcome
                        decorated={appearance.skin === "catgirl"}
                        loading={loading}
                        busy={!!busy || !canCreate}
                        onImport={() => upload.current?.click()}
                        onModels={openModelLibrary}
                      />
                    )}
                  </div>
                  {previewJob && (
                    <OperationProgress
                      title={previewJob.message || "正在准备预览"}
                      percent={
                        typeof previewJob.progress === "number"
                          ? Math.min(99, Math.floor(previewJob.progress * 100))
                          : null
                      }
                      detail={previewJob.message}
                    />
                  )}
                  {project && (
                    <Timeline
                      duration={project.duration_ms}
                      now={now}
                      clip={clip}
                      fps={fps}
                      disabled={!!busy || !canEdit}
                      onSeek={seek}
                      onBoundary={boundary}
                    />
                  )}
                  <div className="preview-footer">
                    <span>
                      <span className="live-position" /> 播放位置{" "}
                      <code>{format(now)}</code>
                    </span>
                    <span className="small muted">
                      {!project?.has_video
                        ? "原始音频"
                        : quality === "low"
                          ? "省流预览 · 最高320p"
                          : `高清预览 · 最高${Math.min(1080, project.source_height || 1080)}p`}
                    </span>
                  </div>
                </section>
                <section className="panel trim-panel">
                  <div className="panel-heading">
                    <h3>
                      <Icon name="cut" size={17} /> 片段边界
                    </h3>
                    {clip && (
                      <span className="duration-chip">
                        时长 {length(clip.end_ms - clip.start_ms)}
                      </span>
                    )}
                  </div>
                  {clip && project ? (
                    <>
                      <div className="boundary-controls">
                        {(["start_ms", "end_ms"] as const).map((edge, i) => (
                          <div className="boundary" key={`${clip.id}-${edge}`}>
                            <div className="boundary-label">
                              <label htmlFor={edge}>
                                {i === 0 ? "入点" : "出点"}
                                <span>{i === 0 ? "I" : "O"}</span>
                              </label>
                              <button
                                onClick={() => boundary(edge, now)}
                                disabled={!!busy || !canEdit}
                              >
                                使用播放位置
                              </button>
                            </div>
                            <div className="time-input timestamp-wrap">
                              <TimecodeInput
                                id={edge}
                                value={clip[edge]}
                                min={i === 0 ? 0 : clip.start_ms + 1}
                                max={
                                  i === 0
                                    ? clip.end_ms - 1
                                    : project.duration_ms
                                }
                                disabled={!!busy || !canEdit}
                                onChange={(value) => boundary(edge, value)}
                                onValidity={(invalid) =>
                                  setInvalidTimes((previous) =>
                                    previous[edge] === invalid
                                      ? previous
                                      : { ...previous, [edge]: invalid },
                                  )
                                }
                              />
                              <button
                                onClick={() => seek(clip[edge])}
                                title="跳转到此位置"
                                aria-label={`跳转到${i === 0 ? "入点" : "出点"}`}
                              >
                                <Icon name="play" size={14} />
                              </button>
                            </div>
                            <div className="frame-nudges">
                              <button
                                disabled={!!busy || !canEdit}
                                aria-label={`${i === 0 ? "入点" : "出点"}前移一帧`}
                                onClick={() =>
                                  boundary(
                                    edge,
                                    stepFrames(clip[edge], -1, 1, fps),
                                  )
                                }
                              >
                                ← 1 帧
                              </button>
                              <button
                                disabled={!!busy || !canEdit}
                                aria-label={`${i === 0 ? "入点" : "出点"}后移一帧`}
                                onClick={() =>
                                  boundary(
                                    edge,
                                    stepFrames(clip[edge], 1, 1, fps),
                                  )
                                }
                              >
                                1 帧 →
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                      <p className="trim-note">
                        时间格式
                        HH:MM:SS.mmm。拖动或微调边界不会移动已有歌词时间。
                      </p>
                    </>
                  ) : (
                    <div className="empty-trim">
                      选择或添加一个片段，开始调整入点与出点。
                    </div>
                  )}
                </section>
                <section className="panel jobs-panel">
                  <div className="panel-heading">
                    <h3>
                      <Icon name="download" size={17} /> 任务与导出
                    </h3>
                    <span className="small muted">自动更新</span>
                  </div>
                  {activeJobs.length ? (
                    <div className="job-list">
                      {activeJobs.map((j) => (
                        <div className="job" key={j.id}>
                          <div className="job-top">
                            <strong>{jobLabels[j.kind] || j.kind}</strong>
                            <span className={`status ${j.status}`}>
                              {statuses[j.status]}
                            </span>
                          </div>
                          <p>{j.message || "等待任务处理"}</p>
                          {["queued", "running"].includes(j.status) &&
                            (canManage ||
                              (!!caps?.user && j.owner === caps.user)) && (
                              <button
                                className="button"
                                onClick={() => void cancelJob(j)}
                              >
                                取消任务
                              </button>
                            )}
                          {(j.model_name || j.model_id) && (
                            <p>
                              实际检测模型：
                              {j.model_name || j.model_id?.slice(0, 12)}
                              {j.device ? ` · ${j.device}` : ""}
                            </p>
                          )}
                          {j.status === "succeeded" &&
                            j.kind === "export" &&
                            j.artifacts.length > 0 && (
                              <div className="downloads">
                                {j.artifacts
                                  .filter((name) => /\.(mp4|mp3)$/i.test(name))
                                  .map((name) => (
                                    <a
                                      key={name}
                                      href={api.artifact(j, name)}
                                      download
                                    >
                                      <Icon name="download" size={14} />
                                      {name.toLowerCase().endsWith(".mp4")
                                        ? "下载视频 · MP4"
                                        : "下载音频 · MP3"}
                                    </a>
                                  ))}
                                {j.artifacts.some(
                                  (name) => !/\.(mp4|mp3)$/i.test(name),
                                ) && (
                                  <details className="export-extras">
                                    <summary>附加文件</summary>
                                    {j.artifacts
                                      .filter(
                                        (name) => !/\.(mp4|mp3)$/i.test(name),
                                      )
                                      .map((name) => (
                                        <a
                                          key={name}
                                          href={api.artifact(j, name)}
                                          download
                                        >
                                          {name}
                                        </a>
                                      ))}
                                  </details>
                                )}
                              </div>
                            )}
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="empty-jobs">
                      导出的视频与音频会出现在这里。
                    </div>
                  )}
                </section>
              </div>
              <section className="panel inspector">
                <div className="inspector-tabs">
                  <button
                    className={tab === "details" ? "active" : ""}
                    onClick={() => setTab("details")}
                  >
                    歌曲信息
                  </button>
                  <button
                    className={tab === "lyrics" ? "active" : ""}
                    onClick={() => setTab("lyrics")}
                  >
                    歌词与字幕
                  </button>
                  <button
                    className={tab === "export" ? "active" : ""}
                    onClick={() => setTab("export")}
                  >
                    导出设置
                  </button>
                </div>
                {clip ? (
                  <>
                    <div className="inspector-content">
                      {tab === "export" ? (
                        <ExportControls
                          value={output}
                          hasVideo={project?.has_video}
                          lowResource={caps?.low_resource}
                          sourceHeight={project?.source_height}
                          sourceFps={project?.source_fps}
                          subtitleEnabled={clip.subtitle.enabled}
                          disabled={!!busy || !canEdit}
                          onChange={(export_settings) =>
                            updateClip({ export_settings })
                          }
                        />
                      ) : tab === "details" ? (
                        <>
                          <p className="inspector-note">
                            歌名和原唱请手动填写。
                          </p>
                          <label className="field">
                            歌曲名称
                            <input
                              value={clip.title}
                              placeholder="填写歌曲名称"
                              disabled={!!busy || !canEdit}
                              onChange={(e) =>
                                updateClip({ title: e.target.value })
                              }
                            />
                          </label>
                          <label className="field">
                            原唱
                            <input
                              value={clip.original_artist}
                              placeholder="填写原唱歌手"
                              disabled={!!busy || !canEdit}
                              onChange={(e) =>
                                updateClip({ original_artist: e.target.value })
                              }
                            />
                          </label>
                          <label className="field">
                            演唱者 / VTuber
                            <input
                              value={clip.performer}
                              placeholder="填写本场演唱者"
                              disabled={!!busy || !canEdit}
                              onChange={(e) =>
                                updateClip({ performer: e.target.value })
                              }
                            />
                          </label>
                          <div className="clip-facts">
                            <span>
                              片段来源
                              <b>
                                {clip.detection === "manual"
                                  ? "手动添加"
                                  : "演唱检测"}
                              </b>
                            </span>
                            <span>
                              歌词行数<b>{clip.lyrics.length} 行</b>
                            </span>
                          </div>
                          <button
                            className="delete-button"
                            disabled={!!busy || processing || !canEdit}
                            onClick={() => {
                              if (
                                window.confirm(
                                  "删除这个歌曲片段？原始素材会保留。",
                                )
                              ) {
                                setProject((p) =>
                                  p
                                    ? {
                                        ...p,
                                        clips: p.clips.filter(
                                          (c) => c.id !== clip.id,
                                        ),
                                      }
                                    : p,
                                );
                                setSelected(
                                  project?.clips.find((c) => c.id !== clip.id)
                                    ?.id || "",
                                );
                              }
                            }}
                          >
                            删除此片段
                          </button>
                        </>
                      ) : (
                        <>
                          <label className="file-pick">
                            导入 .lrc 文件
                            <input
                              type="file"
                              accept=".lrc,text/plain"
                              onChange={async (e) => {
                                const file = e.target.files?.[0];
                                if (file) {
                                  try {
                                    setLrc(await file.text());
                                  } catch (err) {
                                    fail(err);
                                  }
                                }
                                e.target.value = "";
                              }}
                            />
                          </label>
                          <label className="field">
                            LRC 歌词
                            <textarea
                              rows={5}
                              value={lrc}
                              onChange={(e) => setLrc(e.target.value)}
                              placeholder={
                                "[00:12.50]第一句歌词\n[00:18.20]第二句歌词"
                              }
                            />
                          </label>
                          <label className="field">
                            歌词偏移（秒）
                            <input
                              type="number"
                              step="0.1"
                              value={offset}
                              onChange={(e) =>
                                setOffset(Number(e.target.value))
                              }
                            />
                            <small>歌词时间相对片段入点，正值延后显示。</small>
                          </label>
                          <button
                            className="button full"
                            disabled={blocked || !lrc.trim()}
                            onClick={() => void importLyrics()}
                          >
                            导入歌词到此片段
                          </button>
                          <div className="section-rule" />
                          <label className="toggle-row">
                            <span>导出时烧录字幕</span>
                            <input
                              type="checkbox"
                              checked={clip.subtitle.enabled}
                              disabled={
                                !!busy || !canEdit || !caps?.subtitle_burn
                              }
                              onChange={(e) =>
                                updateClip({
                                  subtitle: {
                                    ...clip.subtitle,
                                    enabled: e.target.checked,
                                  },
                                })
                              }
                            />
                          </label>
                          {!caps?.subtitle_burn && (
                            <p className="small muted">
                              当前环境尚不支持字幕烧录。
                            </p>
                          )}
                          <label className="field">
                            字幕样式
                            <select
                              value={clip.subtitle.layout}
                              disabled={!!busy || !canEdit}
                              onChange={(e) =>
                                updateClip({
                                  subtitle: {
                                    ...clip.subtitle,
                                    layout: e.target
                                      .value as Clip["subtitle"]["layout"],
                                  },
                                })
                              }
                            >
                              <option value="single">单行字幕</option>
                              <option value="double">双行字幕</option>
                              <option value="ktv">左右双行（KTV）</option>
                            </select>
                          </label>
                          <div className="field-row">
                            <label className="field">
                              字号
                              <input
                                type="number"
                                min={16}
                                max={100}
                                value={clip.subtitle.font_size}
                                disabled={!!busy || !canEdit}
                                onChange={(e) =>
                                  updateClip({
                                    subtitle: {
                                      ...clip.subtitle,
                                      font_size: Number(e.target.value),
                                    },
                                  })
                                }
                              />
                            </label>
                            <label className="field">
                              底部边距
                              <input
                                type="number"
                                min={0}
                                max={400}
                                value={clip.subtitle.margin_bottom}
                                disabled={!!busy || !canEdit}
                                onChange={(e) =>
                                  updateClip({
                                    subtitle: {
                                      ...clip.subtitle,
                                      margin_bottom: Number(e.target.value),
                                    },
                                  })
                                }
                              />
                            </label>
                          </div>
                          <div className="field-row color-fields">
                            <label className="field">
                              文字颜色
                              <input
                                type="color"
                                value={clip.subtitle.color}
                                disabled={!!busy || !canEdit}
                                onChange={(e) =>
                                  updateClip({
                                    subtitle: {
                                      ...clip.subtitle,
                                      color: e.target.value,
                                    },
                                  })
                                }
                              />
                            </label>
                            <label className="field">
                              高亮颜色
                              <input
                                type="color"
                                value={clip.subtitle.highlight}
                                disabled={!!busy || !canEdit}
                                onChange={(e) =>
                                  updateClip({
                                    subtitle: {
                                      ...clip.subtitle,
                                      highlight: e.target.value,
                                    },
                                  })
                                }
                              />
                            </label>
                          </div>
                          <p className="small muted">
                            预览显示当前歌词行，最终字幕样式以导出视频为准。
                          </p>
                          <p className="inspector-note">
                            KTV 逐字高亮需要词级时间；普通 LRC 按行显示。
                          </p>
                          {clip.lyrics.length > 0 && (
                            <details className="lyrics-list">
                              <summary>
                                已导入 {clip.lyrics.length} 行歌词
                              </summary>
                              {clip.lyrics.map((l, i) => (
                                <button
                                  key={i}
                                  onClick={() => seek(l.start_ms)}
                                >
                                  <code>{format(l.start_ms).slice(0, -4)}</code>
                                  {l.text}
                                </button>
                              ))}
                            </details>
                          )}
                        </>
                      )}
                    </div>
                    <div className="export-area">
                      <button
                        className="button primary full"
                        disabled={
                          blocked ||
                          !!exportIssue ||
                          !caps?.ffmpeg ||
                          (!output.video && !output.audio)
                        }
                        onClick={() => void run("export")}
                      >
                        <Icon name="download" size={17} /> 导出 {outputLabel}
                      </button>
                      {dirty && (
                        <button
                          className="button full"
                          disabled={!!busy || conflict || invalidTime}
                          onClick={() => void save()}
                        >
                          保存修改
                        </button>
                      )}
                      <p>
                        {exportIssue ||
                          (dirty
                            ? "先保存修改，再导出当前片段"
                            : processing
                              ? "任务处理中，完成后可继续操作"
                              : capabilitiesLoading
                                ? "正在检查媒体工具…"
                                : capabilityError && !caps
                                  ? "媒体工具检查失败，请在上方重试"
                                  : !caps?.ffmpeg
                                    ? "请先安装 FFmpeg 以启用导出"
                                    : "当前片段 · 输出文件保存在本地")}
                      </p>
                    </div>
                  </>
                ) : (
                  <div className="inspector-empty">
                    <Icon name="settings" size={32} />
                    <h3>选择一个歌曲片段</h3>
                    <p>在左侧添加或选中片段后编辑。</p>
                  </div>
                )}
              </section>
            </div>
          </div>
          <RecordingsWorkspace
            active={sessionActive && workspace === "recordings"}
            admin={canManage}
            onOpen={async (id) => {
              if (await load(id, true)) switchWorkspace("cut");
            }}
          />
          {canTrain ? (
            <TrainingWorkspace
              active={sessionActive && workspace === "training"}
              catalog={catalog}
              onModelsChanged={() => void refreshCatalog()}
              onUseModel={(model) => void useModel(model)}
              modelManager={
                <ModelManager
                  catalog={catalog}
                  onImported={async () => {
                    await refreshCatalog();
                  }}
                  onUse={(model) => void useModel(model)}
                />
              }
            />
          ) : (
            <section
              className="server-models"
              hidden={workspace !== "training"}
              aria-label="模型管理工作区"
            >
              <h2>模型管理</h2>
              <p>模型训练请在本地完成，服务器使用训练好的模型检测演唱片段。</p>
              {canManage ? (
                <ModelManager
                  catalog={catalog}
                  onImported={async () => {
                    await refreshCatalog();
                  }}
                  onUse={(model) => void useModel(model)}
                />
              ) : (
                <p>请联系管理员导入模型。已有模型可以在切歌工作区选择。</p>
              )}
            </section>
          )}
          <footer className="app-footer">
            <span>拾歌 · VTuber SongCut</span>
            {canManage && (
              <button
                className="cache-link"
                aria-haspopup="dialog"
                onClick={() => setCacheOpen(true)}
              >
                缓存管理
              </button>
            )}
            <a href="/help/faq" target="_blank" rel="noreferrer">
              使用帮助 ↗
            </a>
          </footer>
        </main>
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AuthGate>
      {(active, logout) => <App sessionActive={active} onLogout={logout} />}
    </AuthGate>
  </React.StrictMode>,
);
