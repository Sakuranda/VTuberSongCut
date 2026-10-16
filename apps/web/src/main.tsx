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
import { TrainingWorkspace } from "./TrainingWorkspace";
import { UploadFeedback } from "./UploadFeedback";
import type { FileUploadState } from "./UploadFeedback";
import { OperationProgress } from "./OperationProgress";
import "./styles.css";
import "./training.css";

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
function App() {
  const [workspace, setWorkspace] = useState<"cut" | "training">("cut");
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
  const [lrc, setLrc] = useState("");
  const [offset, setOffset] = useState(0);
  const [tab, setTab] = useState<"details" | "lyrics" | "export">("details");
  const [loading, setLoading] = useState(true);
  const [invalidTimes, setInvalidTimes] = useState<Record<string, boolean>>({});
  const invalidTime = Object.values(invalidTimes).some(Boolean);
  const media = useRef<HTMLVideoElement | null>(null);
  const upload = useRef<HTMLInputElement | null>(null);
  const pendingModelId = useRef<string | null>(null);
  const dirty = !!project && JSON.stringify(projectEdits(project)) !== saved;
  const live = useRef({ project, dirty, busy });
  live.current = { project, dirty, busy };
  const previousJobs = useRef<Job[]>([]);
  const clip = project?.clips.find((c) => c.id === selected);
  const activeJobs = jobs.filter((j) => j.project_id === project?.id);
  const previewJob = activeJobs.find(
    (job) =>
      job.kind === "preview" && ["queued", "running"].includes(job.status),
  );
  const processing = activeJobs.some(
    (j) => j.status === "running" || j.status === "queued",
  );
  const blocked = !!busy || dirty || conflict || processing || invalidTime;
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
  function switchWorkspace(next: "cut" | "training") {
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
    void refreshCatalog();
  }, []);
  const fps = editingFps(project?.source_fps);
  const output = clip?.export_settings || DEFAULT_EXPORT_SETTINGS;
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
  async function load(id: string) {
    if (
      live.current.dirty &&
      !window.confirm("当前修改尚未保存。放弃修改并打开项目？")
    )
      return;
    setBusy("打开项目");
    setError("");
    try {
      accept(await api.project(id), false);
      setMediaError(false);
      setNow(0);
    } catch (e) {
      fail(e);
    } finally {
      setBusy("");
    }
  }
  function fail(e: unknown) {
    setError(e instanceof Error ? e.message : "操作未完成，请重试");
  }
  useEffect(() => {
    let stopped = false;
    Promise.all([api.projects(), api.capabilities()])
      .then(([list, capabilities]) => {
        if (stopped) return;
        setProjects(list);
        setCaps(capabilities);
        if (list.length) accept(list[0], false);
      })
      .catch((e) => {
        if (!stopped) fail(e);
      })
      .finally(() => {
        if (!stopped) setLoading(false);
      });
    return () => {
      stopped = true;
    };
  }, []);
  useEffect(() => {
    let stopped = false;
    async function poll() {
      try {
        const items = await api.jobs();
        if (stopped) return;
        setJobs(items);
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
        previousJobs.current = items;
        if (changed && p) {
          if (live.current.dirty || live.current.busy) setRefreshPending(true);
          else {
            const fresh = await api.project(p.id);
            if (!stopped && live.current.project?.id === fresh.id) {
              if (!live.current.dirty && !live.current.busy) accept(fresh);
              else setRefreshPending(true);
            }
          }
        }
      } catch {
        /* Keep editing available during a temporary polling failure. */
      }
    }
    void poll();
    const timer = window.setInterval(poll, 2000);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, []);
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
    if (!project || !clip) return;
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
    if (media.current) media.current.currentTime = bounded / 1000;
  }
  function boundary(edge: "start_ms" | "end_ms", ms: number) {
    if (!clip || !project || !Number.isFinite(ms)) return;
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
        if (!clip || busy || invalidTime) return;
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
  }, [workspace, project, clip, now, busy, invalidTime, fps, mediaError]);
  async function save() {
    if (!project) return;
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
    if (!file) return;
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
          ? "视频已导入，并选中刚才的模型。保存修改后可开始检测。"
          : "视频已导入，选择模型后可检测演唱片段，也可手动添加。",
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
    if (!project) return;
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
      export_settings: { ...DEFAULT_EXPORT_SETTINGS },
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
          <div className="brand">
            <h1>
              拾歌<span>VTuber SongCut</span>
            </h1>
            <p>直播歌切工作台</p>
          </div>
          <div className="top-actions" hidden={workspace !== "cut" || !project}>
            <span className="local-label">工作空间</span>
            <button
              className="button primary"
              disabled={!!busy || processing}
              onClick={() => upload.current?.click()}
            >
              <Icon name="upload" size={17} />
              {busy.includes("导入视频") ? "正在导入…" : "导入视频"}
            </button>
            <input
              ref={upload}
              hidden
              type="file"
              accept=".mp4,.flv,.mkv,.mov,.webm,.avi,.ts,.m4v,.mp3,.wav,.m4a,.flac,.aac,.ogg,.opus,video/*,audio/*"
              onChange={(e) => void importVideo(e.target.files?.[0])}
            />
          </div>
        </header>
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
            模型训练
          </button>
        </nav>
        <main>
          <div hidden={workspace !== "cut"} aria-label="录播切歌工作区">
            <div className="page-heading">
              <div>
                <h2>{project ? project.name : "录播切歌"}</h2>
              </div>
              {project && (
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
              disabled={!!busy || processing}
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
                  onImport={openModelLibrary}
                  project={project}
                  catalog={catalog}
                  disabled={!!busy || processing}
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
                    disabled={!project || !!busy || processing}
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
                    <span className="small muted">
                      {project ? "原始素材时间" : "等待导入素材"}
                    </span>
                  </div>
                  <div
                    className={`video-stage ${!project ? "empty-stage" : ""}`}
                  >
                    {project ? (
                      <>
                        <video
                          key={`${project.id}-${project.preview_file}`}
                          ref={media}
                          src={api.media(project)}
                          controls
                          preload="metadata"
                          onTimeUpdate={(e) =>
                            setNow(
                              Math.round(e.currentTarget.currentTime * 1000),
                            )
                          }
                          onError={() => setMediaError(true)}
                          onLoadedData={() => setMediaError(false)}
                        />
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
                        {mediaError && (
                          <div className="media-error">
                            <strong>浏览器暂时无法播放此素材</strong>
                            <p>生成兼容预览后继续剪辑，原始素材会用于导出。</p>
                            <button
                              className="button primary"
                              disabled={blocked || !caps?.ffmpeg}
                              onClick={() => void run("preview")}
                            >
                              生成兼容预览
                            </button>
                          </div>
                        )}
                      </>
                    ) : (
                      <div className="stage-invite">
                        <div className="record-art">
                          <div />
                          <Icon name="music" size={38} />
                        </div>
                        <h3>
                          {loading
                            ? "正在连接本地工作空间…"
                            : "导入录播，开始切歌"}
                        </h3>
                        <p>
                          支持 MP4、FLV、MKV
                          等视频及音频。导入后可自动检测或手动切片。
                        </p>
                        <button
                          className="button primary"
                          disabled={!!busy || loading}
                          onClick={() => upload.current?.click()}
                        >
                          <Icon name="upload" size={17} /> 导入录播
                        </button>
                        <button
                          className="welcome-model-link"
                          onClick={openModelLibrary}
                        >
                          已有主播模型？先导入模型
                        </button>
                      </div>
                    )}
                  </div>
                  {previewJob && (
                    <OperationProgress
                      title="正在生成兼容预览"
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
                      disabled={!!busy}
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
                      {project?.preview_file ? "兼容预览" : "原始画面"}
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
                                disabled={!!busy}
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
                                disabled={!!busy}
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
                                disabled={!!busy}
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
                                disabled={!!busy}
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
                      {[...activeJobs].reverse().map((j) => (
                        <div className="job" key={j.id}>
                          <div className="job-top">
                            <strong>{jobLabels[j.kind] || j.kind}</strong>
                            <span className={`status ${j.status}`}>
                              {statuses[j.status]}
                            </span>
                          </div>
                          <p>{j.message || "等待任务处理"}</p>
                          {(j.model_name || j.model_id) && (
                            <p>
                              实际检测模型：
                              {j.model_name || j.model_id?.slice(0, 12)}
                              {j.device ? ` · ${j.device}` : ""}
                            </p>
                          )}
                          {j.status === "succeeded" &&
                            j.artifacts.length > 0 && (
                              <div className="downloads">
                                {j.artifacts.map((name) => (
                                  <a
                                    key={name}
                                    href={api.artifact(j, name)}
                                    download
                                  >
                                    <Icon name="download" size={14} />
                                    {name}
                                  </a>
                                ))}
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
                          disabled={!!busy}
                          onChange={(export_settings) =>
                            updateClip({ export_settings })
                          }
                        />
                      ) : tab === "details" ? (
                        <>
                          <div className="inspector-intro">
                            <span className="eyebrow">CLIP DETAILS</span>
                            <h3>让每首歌，都有名字。</h3>
                          </div>
                          <label className="field">
                            歌曲名称
                            <input
                              value={clip.title}
                              placeholder="填写歌曲名称"
                              disabled={!!busy}
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
                              disabled={!!busy}
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
                              disabled={!!busy}
                              onChange={(e) =>
                                updateClip({ performer: e.target.value })
                              }
                            />
                          </label>
                          <div className="pending-feature">
                            <span className="tiny-tag">待接入</span>
                            <strong>自动识曲</strong>
                            <p>
                              当前请手动填写歌曲信息。
                              <br />
                              系统不会生成未验证的识曲结果。
                            </p>
                          </div>
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
                            disabled={!!busy || processing}
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
                          <div className="inspector-intro">
                            <span className="eyebrow">LYRICS & SUBTITLES</span>
                            <h3>为歌声，添上字幕。</h3>
                          </div>
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
                              disabled={!!busy || !caps?.subtitle_burn}
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
                              disabled={!!busy}
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
                                disabled={!!busy}
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
                                disabled={!!busy}
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
                                disabled={!!busy}
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
                                disabled={!!busy}
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
                          <div className="pending-feature compact">
                            <span className="tiny-tag">待接入</span>
                            <strong>自动逐字对齐</strong>
                            <p>
                              KTV 样式仅在存在逐字时间时逐字高亮；普通 LRC
                              使用整行时间。
                            </p>
                          </div>
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
                        {dirty
                          ? "先保存修改，再导出当前片段"
                          : processing
                            ? "任务处理中，完成后可继续操作"
                            : !caps?.ffmpeg
                              ? "请先安装 FFmpeg 以启用导出"
                              : "当前片段 · 输出文件保存在本地"}
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
          <TrainingWorkspace
            active={workspace === "training"}
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
          <footer className="app-footer">
            <span>拾歌 · VTuber SongCut</span>
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
    <App />
  </React.StrictMode>,
);
