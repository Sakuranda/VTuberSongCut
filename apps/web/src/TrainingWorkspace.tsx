import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { api } from "./api";
import type {
  ModelCatalog,
  ModelInfo,
  TrainingDataset,
  TrainingJob,
  TrainingSample,
  TrainingSettings,
} from "./api";
import { TrainingAnnotation } from "./TrainingAnnotation";
import { usePolling } from "./usePolling";
import { UploadFeedback } from "./UploadFeedback";
import type { FileUploadState } from "./UploadFeedback";

const defaults: TrainingSettings = {
  device: "auto",
  steps: 2000,
  batch_size: 2,
  window_s: 32,
  val_every: 100,
  lr: 0.0003,
  seed: 42,
  warmup: 100,
};
const jobStatus: Record<TrainingJob["status"], string> = {
  queued: "等待中",
  running: "训练中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};
const stageLabels: Record<string, string> = {
  queued: "等待准备",
  preparing: "准备音频与标注",
  training: "训练与验证",
  registering: "保存模型",
  finished: "任务结束",
};
const messageOf = (error: unknown) =>
  error instanceof Error ? error.message : "操作未完成，请重试";
export function trainingReadiness(dataset: TrainingDataset | null): string[] {
  if (!dataset) return ["先创建训练项目"];
  const reasons: string[] = [];
  if (dataset.samples.length < 2) reasons.push("至少上传两份不同的录播");
  if (dataset.samples.some((sample) => !sample.reviewed))
    reasons.push("完整复核并保存每一份录播的标注");
  const seen = new Set<string>();
  if (
    dataset.samples.some((sample) => {
      const duplicate = seen.has(sample.sha256);
      seen.add(sample.sha256);
      return duplicate;
    })
  )
    reasons.push("每份录播只上传一次，训练和验证不能使用同一文件");
  for (const [split, title] of [
    ["train", "训练用途"],
    ["validation", "验证用途"],
  ] as const) {
    const samples = dataset.samples.filter((sample) => sample.split === split);
    if (!samples.length) {
      reasons.push(`至少一份独立录播设为${title}`);
      continue;
    }
    let positive = 0,
      frames = 0;
    for (const sample of samples) {
      const count = Math.floor((sample.duration_ms * 3.125) / 1000);
      frames += count;
      for (const region of sample.regions) {
        if (region.label === "target")
          positive += Math.max(
            0,
            Math.min(count, Math.ceil((region.end_ms * 3.125) / 1000 - 0.5)) -
              Math.max(0, Math.ceil((region.start_ms * 3.125) / 1000 - 0.5)),
          );
      }
    }
    if (!positive || positive >= frames)
      reasons.push(`${title}中都需要目标主播演唱和非目标演唱的区间`);
  }
  return reasons;
}

export function TrainingWorkspace({
  active,
  catalog,
  modelManager,
  onUseModel,
  onModelsChanged,
}: {
  active: boolean;
  catalog: ModelCatalog | null;
  modelManager: ReactNode;
  onUseModel: (model: ModelInfo) => void;
  onModelsChanged: () => void;
}) {
  const [datasets, setDatasets] = useState<TrainingDataset[]>([]);
  const [dataset, setDataset] = useState<TrainingDataset | null>(null);
  const [sample, setSample] = useState<TrainingSample | null>(null);
  const [jobs, setJobs] = useState<TrainingJob[]>([]);
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [settings, setSettings] = useState<TrainingSettings>(defaults);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [pollError, setPollError] = useState("");
  const [uploadState, setUploadState] = useState<FileUploadState | null>(null);
  const upload = useRef<HTMLInputElement>(null);
  const loaded = useRef(false);
  const knownModels = useRef(new Set<string>());
  useEffect(() => {
    let changed = false;
    for (const job of jobs) {
      if (
        job.status === "succeeded" &&
        job.model &&
        !knownModels.current.has(job.model.id)
      ) {
        knownModels.current.add(job.model.id);
        changed = true;
      }
    }
    if (changed) onModelsChanged();
  }, [jobs]);
  const dirty =
    !!sample &&
    JSON.stringify(sample) !==
      JSON.stringify(dataset?.samples.find((item) => item.id === sample.id));
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty || busy === "导入训练录播";
  const currentJobs = jobs.filter((job) => job.dataset_id === dataset?.id);
  const running = jobs.some((job) =>
    ["queued", "running"].includes(job.status),
  );
  const reasons = trainingReadiness(dataset);
  const validSettings =
    Number.isInteger(settings.steps) &&
    settings.steps >= 1 &&
    settings.steps <= 50000 &&
    Number.isInteger(settings.batch_size) &&
    settings.batch_size >= 1 &&
    settings.batch_size <= 32 &&
    settings.window_s >= 1.28 &&
    settings.window_s <= 192 &&
    Number.isInteger(settings.val_every) &&
    settings.val_every >= 1 &&
    settings.val_every <= 10000 &&
    settings.lr >= 1e-7 &&
    settings.lr <= 0.1 &&
    Number.isInteger(settings.seed) &&
    settings.seed >= 0 &&
    settings.seed <= 4294967295 &&
    Number.isInteger(settings.warmup) &&
    settings.warmup >= 0 &&
    settings.warmup <= 10000;
  function accept(next: TrainingDataset, selectedId = sample?.id) {
    if (dataset?.id !== next.id) setUploadState(null);
    setDataset(next);
    setDatasets((items) => [
      next,
      ...items.filter((item) => item.id !== next.id),
    ]);
    setSample(
      next.samples.find((item) => item.id === selectedId) ||
        next.samples[0] ||
        null,
    );
  }
  useEffect(() => {
    if (!active || loaded.current) return;
    loaded.current = true;
    let stopped = false;
    let settled = false;
    api
      .trainingDatasets()
      .then((items) => {
        if (stopped) return;
        setDatasets(items);
        if (items.length) {
          setDataset(items[0]);
          setSample(items[0].samples[0] || null);
        }
      })
      .catch((err) => {
        if (!stopped) {
          setError(messageOf(err));
          loaded.current = false;
        }
      })
      .finally(() => {
        settled = true;
        if (!stopped) setLoading(false);
      });
    return () => {
      stopped = true;
      if (!settled) loaded.current = false;
    };
  }, [active]);
  usePolling(
    async (signal) => {
      try {
        const items = await api.trainingJobs(signal);
        if (!signal.aborted) {
          setJobs(items);
          setPollError("");
        }
      } catch {
        if (!signal.aborted) setPollError("无法更新训练进度，正在重试。");
      }
    },
    active,
    running ? 2500 : 10000,
  );
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirtyRef.current) event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);
  async function operation(label: string, action: () => Promise<void>) {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (err) {
      setError(messageOf(err));
    } finally {
      setBusy("");
    }
  }
  function canSwitch() {
    return (
      !dirty || window.confirm("当前标注尚未保存。放弃这份标注的修改并切换？")
    );
  }
  async function create() {
    if (!target.trim() || !canSwitch()) return;
    await operation("创建训练项目", async () => {
      accept(
        await api.createTrainingDataset(
          name.trim() || `${target.trim()} · 演唱模型`,
          target.trim(),
        ),
        "",
      );
      setName("");
      setTarget("");
      setCreating(false);
      setNotice("项目已创建，可以导入录播了。");
    });
  }
  async function saveSample() {
    if (!dataset || !sample) return;
    await operation("保存标注", async () => {
      accept(await api.saveTrainingSample(dataset, sample));
      setNotice("标注已保存。");
    });
  }
  async function importSample(file?: File) {
    if (!file || !dataset || dirty) return;
    setBusy("导入训练录播");
    setError("");
    setNotice("");
    setUploadState({
      phase: "uploading",
      filename: file.name,
      loaded: 0,
      total: file.size,
    });
    try {
      const result = await api.uploadTrainingSample(dataset, file, (progress) =>
        setUploadState({ ...progress, filename: file.name }),
      );
      accept(result, result.samples.at(-1)?.id);
      setUploadState({
        phase: "succeeded",
        filename: file.name,
        loaded: file.size,
        total: file.size,
      });
    } catch (err) {
      setUploadState(
        (state) =>
          state && { ...state, phase: "failed", error: messageOf(err) },
      );
    } finally {
      setBusy("");
      if (upload.current) upload.current.value = "";
    }
  }
  return (
    <section
      className="training-workspace"
      hidden={!active}
      aria-label="模型训练工作区"
    >
      <div className="page-heading">
        <div>
          <h2>模型训练</h2>
          <p className="training-intro">用录播标注训练自己的主播模型。</p>
        </div>
        <div className="training-heading-actions">
          <button
            className="button"
            onClick={() => {
              const library = document.getElementById("model-library");
              library?.focus();
              library?.scrollIntoView({ block: "start" });
            }}
          >
            导入已有模型
          </button>
          <a
            className="text-action"
            href="/help/faq#training"
            target="_blank"
            rel="noreferrer"
          >
            训练帮助 ↗
          </a>
        </div>
      </div>
      {error && (
        <div className="banner error" role="alert">
          <span>{error}</span>
          {dataset && (
            <button
              disabled={!!busy}
              onClick={() => {
                if (canSwitch())
                  void operation("重新载入训练项目", async () =>
                    accept(await api.trainingDataset(dataset.id)),
                  );
              }}
            >
              重新载入项目
            </button>
          )}
          <button onClick={() => setError("")}>关闭</button>
        </div>
      )}
      {notice && (
        <div className="banner info" role="status">
          {notice}
          <button onClick={() => setNotice("")}>关闭</button>
        </div>
      )}
      <div className="training-grid">
        <section
          className="panel training-library"
          aria-label="创建项目与导入录播"
        >
          <div className="panel-heading">
            <h3>
              <span className="training-step">1</span> 创建项目与导入录播
            </h3>
            <button
              className="text-action"
              hidden={!datasets.length}
              disabled={!!busy}
              onClick={() => setCreating(!creating)}
            >
              {creating ? "取消新建" : "新建项目"}
            </button>
          </div>
          {(creating || (!datasets.length && !loading)) && (
            <div className="training-create">
              <label className="field">
                主播名称
                <input
                  maxLength={200}
                  value={target}
                  placeholder="要识别谁的演唱？"
                  disabled={!!busy}
                  onChange={(event) => setTarget(event.target.value)}
                />
              </label>
              <label className="field">
                项目名（选填）
                <input
                  maxLength={200}
                  value={name}
                  placeholder="默认使用主播名称"
                  disabled={!!busy}
                  onChange={(event) => setName(event.target.value)}
                />
              </label>
              <button
                className="button primary"
                disabled={!!busy || !target.trim()}
                onClick={() => void create()}
              >
                创建训练项目
              </button>
            </div>
          )}
          {datasets.length > 0 && (
            <div className="training-project-bar">
              <label className="field training-project-select">
                训练项目
                <select
                  disabled={!!busy || !datasets.length}
                  value={dataset?.id || ""}
                  onChange={(event) => {
                    if (!canSwitch()) return;
                    const id = event.target.value;
                    void operation("打开训练项目", async () =>
                      accept(await api.trainingDataset(id), ""),
                    );
                  }}
                >
                  {!dataset && (
                    <option value="">
                      {loading ? "正在读取…" : "先创建训练项目"}
                    </option>
                  )}
                  {datasets.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
              {dataset && (
                <p className="training-target">
                  主播 <strong>{dataset.target_name}</strong>
                </p>
              )}
            </div>
          )}
          {dataset && (
            <>
              <div className="training-sample-heading">
                <h3>
                  录播素材{" "}
                  <span className="count">{dataset.samples.length}</span>
                </h3>
                <button
                  className="button primary"
                  disabled={!!busy || dirty}
                  onClick={() => upload.current?.click()}
                >
                  {busy === "导入训练录播" ? "正在导入…" : "上传录播"}
                </button>
              </div>
              <input
                hidden
                type="file"
                ref={upload}
                accept=".mp4,.flv,.mkv,.mov,.webm,.avi,.ts,.m4v,.mp3,.wav,.m4a,.flac,video/*,audio/*"
                onChange={(event) => void importSample(event.target.files?.[0])}
              />
              <p className="training-hint library-hint">
                至少两场不同录播，分别用于训练与验证。
                {dirty && "保存当前标注后可继续上传。"}
              </p>
              <div className="training-upload-status">
                <UploadFeedback
                  state={uploadState}
                  onRetry={() => upload.current?.click()}
                  disabled={!!busy || dirty}
                />
              </div>
              <div className="training-samples">
                {dataset.samples.map((item) => (
                  <button
                    key={item.id}
                    disabled={!!busy}
                    className={`training-sample ${sample?.id === item.id ? "selected" : ""}`}
                    onClick={() => {
                      if (item.id !== sample?.id && canSwitch())
                        setSample(item);
                    }}
                  >
                    <strong>{item.name}</strong>
                    <span>
                      {item.split === "train" ? "训练用途" : "验证用途"} ·{" "}
                      {item.reviewed ? "已完整复核" : "待复核"}
                    </span>
                    <small>
                      {item.regions.length} 段标注 ·{" "}
                      {(item.duration_ms / 60000).toFixed(1)} 分钟
                    </small>
                  </button>
                ))}
              </div>
              {!dataset.samples.length && (
                <p className="training-hint library-hint">
                  点击「上传录播」，开始为{dataset.target_name}准备演唱示例。
                </p>
              )}
            </>
          )}
        </section>
        <div className="training-main">
          <section className="panel annotation-panel" hidden={!sample}>
            <div className="panel-heading">
              <h3>
                <span className="training-step">2</span> 预览与标注
                {!sample && " · 先导入素材"}
              </h3>
              {sample && (
                <span className={dirty ? "unsaved" : "saved"}>
                  {dirty ? "● 标注未保存" : "✓ 标注已保存"}
                </span>
              )}
            </div>
            {dataset && sample ? (
              <div className="annotation-body">
                <div className="training-sample-title">
                  <h3 title={sample.name}>{sample.name}</h3>
                  <label className="field">
                    素材用途
                    <select
                      disabled={!!busy}
                      value={sample.split}
                      onChange={(event) =>
                        setSample({
                          ...sample,
                          split: event.target.value as TrainingSample["split"],
                        })
                      }
                    >
                      <option value="train">训练集</option>
                      <option value="validation">验证集</option>
                    </select>
                  </label>
                </div>
                <details className="annotation-help">
                  <summary>如何标注？</summary>
                  <p>
                    标出{dataset.target_name}
                    的每段演唱，其他人或原唱选对应类别。完整复核后，未标注部分会作为非目标演唱。
                  </p>
                </details>
                <TrainingAnnotation
                  active={active}
                  key={`${dataset.id}-${sample.id}`}
                  dataset={dataset}
                  sample={sample}
                  disabled={!!busy}
                  onChange={setSample}
                />
                <label className="review-confirm">
                  <input
                    type="checkbox"
                    checked={sample.reviewed}
                    disabled={!!busy}
                    onChange={(event) =>
                      setSample({ ...sample, reviewed: event.target.checked })
                    }
                  />
                  <span>
                    <strong>已完整复核，目标主播的演唱无遗漏</strong>
                    <small>
                      未标注区间将作为非目标演唱。修改标注后需重新确认。
                    </small>
                  </span>
                </label>
                <div className="annotation-save">
                  <button
                    className="text-action delete-sample"
                    disabled={!!busy || dirty}
                    onClick={() => {
                      if (window.confirm("从训练项目移除此录播和它的标注？"))
                        void operation("移除训练素材", async () =>
                          accept(
                            await api.deleteTrainingSample(dataset, sample),
                            "",
                          ),
                        );
                    }}
                  >
                    移除此录播
                  </button>
                  <button
                    className="button primary"
                    disabled={!dirty || !!busy}
                    onClick={() => void saveSample()}
                  >
                    {busy === "保存标注" ? "正在保存…" : "保存标注"}
                  </button>
                </div>
              </div>
            ) : (
              <div className="training-empty">
                <strong>{dataset ? "先导入录播" : "先创建主播项目"}</strong>
                <p>导入后可在这里试听、标记演唱区间。</p>
              </div>
            )}
          </section>
          <section
            className="panel training-run-panel"
            hidden={!dataset?.samples.length}
          >
            <div className="panel-heading">
              <h3>
                <span className="training-step">3</span> 训练模型
              </h3>
            </div>
            <div className="training-run-body">
              {reasons.length > 0 && (
                <ul className="training-checklist">
                  {reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              )}
              {!reasons.length && dataset && (
                <p className="training-ready">
                  ✓ 素材和复核已准备好，可以开始训练。
                </p>
              )}
              <details className="training-advanced">
                <summary>
                  高级训练设置 <span>默认自动选择设备 · 2,000 步</span>
                </summary>
                <div className="training-settings">
                  <label className="field">
                    运行设备
                    <select
                      disabled={!!busy || running}
                      value={settings.device}
                      onChange={(event) =>
                        setSettings({
                          ...settings,
                          device: event.target
                            .value as TrainingSettings["device"],
                        })
                      }
                    >
                      {!catalog?.devices.length && (
                        <option value="auto">自动选择</option>
                      )}
                      {catalog?.devices.map((device) => (
                        <option
                          key={device.id}
                          value={device.id}
                          disabled={!device.available}
                        >
                          {device.label}
                          {!device.available ? "（不可用）" : ""}
                        </option>
                      ))}
                    </select>
                    <small>自动优先使用可用显卡，否则使用 CPU。</small>
                  </label>
                  {(
                    [
                      {
                        key: "steps",
                        label: "训练步数",
                        min: 1,
                        max: 50000,
                        step: 1,
                      },
                      {
                        key: "batch_size",
                        label: "每批样本数",
                        min: 1,
                        max: 32,
                        step: 1,
                      },
                      {
                        key: "window_s",
                        label: "每段采样秒数",
                        min: 1.28,
                        max: 192,
                        step: 0.32,
                      },
                      {
                        key: "val_every",
                        label: "每隔多少步验证",
                        min: 1,
                        max: 10000,
                        step: 1,
                      },
                      {
                        key: "lr",
                        label: "学习率",
                        min: 1e-7,
                        max: 0.1,
                        step: 0.0001,
                      },
                      {
                        key: "seed",
                        label: "随机种子",
                        min: 0,
                        max: 4294967295,
                        step: 1,
                      },
                      {
                        key: "warmup",
                        label: "预热步数",
                        min: 0,
                        max: 10000,
                        step: 1,
                      },
                    ] as const
                  ).map((field) => (
                    <label className="field" key={field.key}>
                      {field.label}
                      <input
                        type="number"
                        min={field.min}
                        max={field.max}
                        step={field.step}
                        value={settings[field.key]}
                        disabled={!!busy || running}
                        onChange={(event) =>
                          setSettings({
                            ...settings,
                            [field.key]: Number(event.target.value),
                          })
                        }
                      />
                    </label>
                  ))}
                </div>
              </details>
              {!validSettings && (
                <p className="model-warning">请检查高级设置中的数值范围。</p>
              )}
              {dirty && (
                <p className="model-warning">
                  当前标注尚未保存，保存后再开始训练。
                </p>
              )}
              {catalog && !catalog.engine_ready && (
                <p className="model-warning">
                  {catalog.engine_error || "训练环境尚未就绪。"}
                </p>
              )}
              <button
                className="button primary"
                disabled={
                  !!busy ||
                  dirty ||
                  running ||
                  reasons.length > 0 ||
                  !validSettings ||
                  !catalog?.engine_ready
                }
                onClick={() => {
                  if (dataset)
                    void operation("开始训练", async () => {
                      const job = await api.startTraining(dataset, settings);
                      setJobs((list) => [
                        job,
                        ...list.filter((item) => item.id !== job.id),
                      ]);
                      setNotice(
                        "训练已开始。进度和实际日志会显示在下方，可以随时取消。",
                      );
                    });
                }}
              >
                {busy === "开始训练"
                  ? "正在创建任务…"
                  : running
                    ? "已有训练任务进行中"
                    : "开始训练模型"}
              </button>
            </div>
          </section>
          <section
            className="panel training-jobs"
            hidden={!currentJobs.length && !pollError}
          >
            <div className="panel-heading">
              <h3>训练进度</h3>
              <span className="count">{currentJobs.length}</span>
            </div>
            {pollError && (
              <p className="model-warning library-hint" role="status">
                {pollError}
              </p>
            )}
            {!currentJobs.length && (
              <p className="training-hint library-hint">
                开始训练后，这里显示实际准备进度、训练步数与验证记录。
              </p>
            )}
            {currentJobs.map((job) => {
              const latest = job.history.at(-1);
              return (
                <article className="training-job" key={job.id}>
                  <div className="job-top">
                    <strong>{stageLabels[job.stage] || job.stage}</strong>
                    <span className={`status ${job.status}`}>
                      {jobStatus[job.status]}
                    </span>
                  </div>
                  <p>{job.message}</p>
                  <progress
                    value={Math.max(0, Math.min(1, job.progress))}
                    max={1}
                    aria-label="训练总进度"
                  />
                  <div className="training-job-meta">
                    <span>
                      {Math.round(job.progress * 100)}% · 已记录 {job.step} /{" "}
                      {job.total_steps} 步
                    </span>
                    <span>
                      {latest
                        ? `最近验证：第 ${latest.step} 步`
                        : "尚无验证记录"}
                    </span>
                  </div>
                  {latest && (
                    <details className="training-metrics">
                      <summary>查看最近验证记录</summary>
                      <pre>{JSON.stringify(latest, null, 2)}</pre>
                      <p>这是留出的验证录播上的结果，不能代替最终独立测试。</p>
                    </details>
                  )}
                  {job.error && (
                    <p className="model-warning" role="alert">
                      {job.error}
                    </p>
                  )}
                  <details className="training-log">
                    <summary>查看真实训练日志</summary>
                    <pre>{job.log_tail || "等待训练进程输出日志…"}</pre>
                  </details>
                  {["queued", "running"].includes(job.status) && (
                    <button
                      className="button"
                      disabled={!!busy}
                      onClick={() =>
                        void operation("取消训练", async () => {
                          const updated = await api.cancelTraining(job.id);
                          setJobs((list) =>
                            list.map((item) =>
                              item.id === updated.id ? updated : item,
                            ),
                          );
                        })
                      }
                    >
                      取消这次训练
                    </button>
                  )}
                  {job.status === "succeeded" && job.model && (
                    <div className="trained-model-result">
                      <strong>已保存：{job.model.name}</strong>
                      <p>
                        模型已进入模型库。用未参与训练和验证的录播检查识别表现。
                      </p>
                      <button
                        className="button primary"
                        onClick={() => onUseModel(job.model!)}
                      >
                        用此模型去切歌 →
                      </button>
                    </div>
                  )}
                </article>
              );
            })}
          </section>
          {modelManager}
        </div>
      </div>
    </section>
  );
}
