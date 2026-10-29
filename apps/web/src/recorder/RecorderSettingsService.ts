export interface RecorderPreferences {
  recording_quality: string;
  split_mode: "disabled" | "time" | "size";
  split_value: number;
  split_by_title: boolean;
  record_danmaku: boolean;
  record_danmaku_super_chat: boolean;
  record_danmaku_gift: boolean;
  record_danmaku_guard: boolean;
  save_cover: boolean;
  retry_seconds: number;
  check_interval_seconds: number;
}
export type RecorderSettingsReply =
  | {
      available: true;
      message: string;
      adapter_version: string;
      revision: string;
      settings: RecorderPreferences;
      cookie_configured: boolean;
      quality_options: { value: string; label: string }[];
      notice: string;
    }
  | { available: false; message: string; adapter_version: string };
export interface SettingsTransport {
  <T>(path: string, init?: RequestInit): Promise<T>;
}
export interface SettingsSnapshot {
  open: boolean;
  loading: boolean;
  saving: boolean;
  reply: RecorderSettingsReply | null;
  draft: Readonly<RecorderPreferences> | null;
  cookie: string;
  clearCookie: boolean;
  dirty: boolean;
  conflict: boolean;
  error: string;
  notice: string;
}
const boolFields = [
  "split_by_title",
  "record_danmaku",
  "record_danmaku_super_chat",
  "record_danmaku_gift",
  "record_danmaku_guard",
  "save_cover",
] as const;
const numberFields = [
  "split_value",
  "retry_seconds",
  "check_interval_seconds",
] as const;
function validReply(value: RecorderSettingsReply): boolean {
  if (
    !value ||
    typeof value.available !== "boolean" ||
    typeof value.message !== "string"
  )
    return false;
  if (!value.available) return true;
  const settings = value.settings;
  return (
    !!settings &&
    typeof settings.recording_quality === "string" &&
    ["disabled", "time", "size"].includes(settings.split_mode) &&
    boolFields.every((key) => typeof settings[key] === "boolean") &&
    numberFields.every(
      (key) =>
        typeof settings[key] === "number" && Number.isFinite(settings[key]),
    ) &&
    /^[0-9a-f]{64}$/.test(value.revision) &&
    typeof value.cookie_configured === "boolean" &&
    Array.isArray(value.quality_options) &&
    value.quality_options.every(
      (x) => typeof x.value === "string" && typeof x.label === "string",
    )
  );
}
export function validateRecorderPreferences(
  value: Readonly<RecorderPreferences>,
): string {
  if (!value.recording_quality.trim()) return "请选择录制画质。";
  if (!Number.isInteger(value.split_value)) return "分段大小或时长必须是整数。";
  if (
    value.split_mode === "time" &&
    (value.split_value < 1 || value.split_value > 1440)
  )
    return "每段时长需为 1–1440 分钟。";
  if (
    value.split_mode === "size" &&
    (value.split_value < 16 || value.split_value > 65536)
  )
    return "每段大小需为 16–65536 MiB。";
  if (
    !Number.isFinite(value.retry_seconds) ||
    value.retry_seconds < 1 ||
    value.retry_seconds > 120
  )
    return "断线重试间隔需为 1–120 秒。";
  if (
    !Number.isInteger(value.check_interval_seconds) ||
    value.check_interval_seconds < 30 ||
    value.check_interval_seconds > 3600
  )
    return "开播检查间隔需为 30–3600 秒。";
  return "";
}

/** Framework-independent editing/session logic. Cookie exists only in memory. */
export class RecorderSettingsService {
  private state: SettingsSnapshot = {
    open: false,
    loading: false,
    saving: false,
    reply: null,
    draft: null,
    cookie: "",
    clearCookie: false,
    dirty: false,
    conflict: false,
    error: "",
    notice: "",
  };
  private original: RecorderPreferences | null = null;
  private listeners = new Set<() => void>();
  private generation = 0;
  private readRequest?: AbortController;
  private writePending = false;
  private active = false;
  constructor(
    private readonly transport: SettingsTransport,
    private readonly onDirty: (dirty: boolean) => void = () => {},
  ) {}
  getSnapshot = (): Readonly<SettingsSnapshot> => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private publish(patch: Partial<SettingsSnapshot> = {}) {
    const next = { ...this.state, ...patch };
    const changed =
      !!next.draft &&
      JSON.stringify(next.draft) !== JSON.stringify(this.original);
    this.state = {
      ...next,
      saving: this.writePending,
      dirty:
        changed ||
        !!next.cookie.trim() ||
        next.clearCookie ||
        this.writePending,
    };
    this.onDirty(this.state.dirty);
    this.listeners.forEach((listener) => listener());
  }
  setActive(active: boolean) {
    this.active = active;
    if (!active) this.suspend();
    else this.publish();
  }
  async open() {
    if (!this.active) return;
    this.publish({ open: true });
    if (!this.state.dirty && !this.writePending) await this.load();
  }
  suspend() {
    this.generation++;
    this.readRequest?.abort();
    this.readRequest = undefined;
    const removedSecret = !!this.state.cookie || this.state.clearCookie;
    this.publish({
      open: false,
      loading: false,
      cookie: "",
      clearCookie: false,
      ...(removedSecret
        ? { notice: "未提交的登录凭据已清除，其他设置草稿仍保留。" }
        : {}),
    });
  }
  close() {
    this.suspend();
  }
  update<K extends keyof RecorderPreferences>(
    key: K,
    value: RecorderPreferences[K],
  ) {
    if (
      !this.active ||
      !this.state.open ||
      !this.state.draft ||
      this.writePending ||
      this.state.loading
    )
      return;
    const draft = { ...this.state.draft, [key]: value };
    if (key === "split_mode" && value === "size" && draft.split_value < 16)
      draft.split_value = 1024;
    if (
      key === "split_mode" &&
      value === "time" &&
      (draft.split_value < 1 || draft.split_value > 1440)
    )
      draft.split_value = 30;
    this.publish({ draft, error: "", notice: "" });
  }
  setCookie(value: string) {
    if (
      !this.active ||
      !this.state.open ||
      this.writePending ||
      this.state.loading
    )
      return;
    this.publish({
      cookie: value,
      clearCookie: value ? false : this.state.clearCookie,
      error: "",
      notice: "",
    });
  }
  setClearCookie(value: boolean) {
    if (
      !this.active ||
      !this.state.open ||
      this.writePending ||
      this.state.loading
    )
      return;
    this.publish({
      clearCookie: value,
      cookie: value ? "" : this.state.cookie,
      error: "",
      notice: "",
    });
  }
  async reload() {
    await this.load();
  }
  private async load() {
    if (!this.active || !this.state.open || this.writePending) return;
    this.readRequest?.abort();
    const controller = new AbortController();
    this.readRequest = controller;
    const generation = ++this.generation;
    this.publish({
      loading: true,
      error: "",
      notice: "",
      cookie: "",
      clearCookie: false,
    });
    try {
      const reply = await this.transport<RecorderSettingsReply>(
        "/recorder/settings",
        { signal: controller.signal },
      );
      if (generation !== this.generation || controller.signal.aborted) return;
      if (!validReply(reply)) throw Error("录播设置响应异常，请重新读取。");
      this.original = reply.available ? { ...reply.settings } : null;
      this.publish({
        reply,
        draft: reply.available ? { ...reply.settings } : null,
        conflict: false,
      });
    } catch (error) {
      if (!controller.signal.aborted && generation === this.generation)
        this.publish({
          error:
            error instanceof Error
              ? error.message
              : "无法读取录制设置，请重试。",
        });
    } finally {
      if (generation === this.generation) {
        this.readRequest = undefined;
        this.publish({ loading: false });
      }
    }
  }
  async save() {
    const { draft, reply } = this.state;
    if (
      !this.active ||
      !this.state.open ||
      !draft ||
      !reply?.available ||
      this.writePending ||
      this.state.loading ||
      this.state.conflict ||
      !this.state.dirty
    )
      return;
    const error = validateRecorderPreferences(draft);
    if (error) {
      this.publish({ error });
      return;
    }
    const generation = this.generation;
    const body = {
      revision: reply.revision,
      settings: { ...draft },
      ...(this.state.cookie.trim() ? { cookie: this.state.cookie } : {}),
      ...(this.state.clearCookie ? { clear_cookie: true } : {}),
    };
    this.writePending = true;
    this.publish({ error: "", notice: "" });
    try {
      const result = await this.transport<RecorderSettingsReply>(
        "/recorder/settings",
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      if (generation !== this.generation) return;
      if (!validReply(result) || !result.available)
        throw Error("保存结果无法确认，请重新读取设置。");
      this.original = { ...result.settings };
      this.publish({
        reply: result,
        draft: { ...result.settings },
        cookie: "",
        clearCookie: false,
        conflict: false,
        notice: "录制设置已保存。",
      });
    } catch (error) {
      if (generation === this.generation)
        this.publish({
          error:
            error instanceof Error
              ? error.message
              : "保存未完成，请重新读取确认。",
          conflict: (error as { status?: number })?.status === 409,
          cookie: "",
          clearCookie: false,
        });
    } finally {
      this.writePending = false;
      this.publish(
        generation === this.generation
          ? {}
          : { notice: "保存操作已结束，请重新读取确认结果。" },
      );
    }
  }
}
