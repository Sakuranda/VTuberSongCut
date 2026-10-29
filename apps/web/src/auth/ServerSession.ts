export type AuthMode = "local" | "totp" | "basic";
export interface SessionUser {
  name: string;
  role: "admin" | "editor" | "viewer";
}
export interface LoginCredentials {
  username: string;
  password: string;
  code: string;
}
export interface SessionSnapshot {
  phase: "checking" | "authenticated" | "locked" | "error";
  server: boolean;
  mode: AuthMode;
  user?: SessionUser;
  setupRequired: boolean;
  busy: boolean;
  error: string;
  workspaceKey?: string;
}
/** Browser transport/session coordination; no React/Vue imports or stored credentials. */
export class ServerSession {
  private state: SessionSnapshot = {
    phase: "checking",
    server: true,
    mode: "totp",
    setupRequired: false,
    busy: false,
    error: "",
  };
  private listeners = new Set<() => void>();
  private generation = 0;
  constructor(
    private readonly transport: typeof fetch = fetch.bind(globalThis),
  ) {}
  readonly getSnapshot = () => this.state;
  readonly subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private publish(patch: Partial<SessionSnapshot>) {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((fn) => fn());
  }
  private accept(value: unknown) {
    if (!value || typeof value !== "object")
      throw Error("登录状态响应异常，请重新连接。");
    const body = value as {
      server?: boolean;
      mode?: AuthMode;
      authenticated?: boolean;
      user?: SessionUser;
      setup_required?: boolean;
    };
    if (
      typeof body.server !== "boolean" ||
      typeof body.authenticated !== "boolean" ||
      !["local", "basic", "totp"].includes(body.mode || "")
    )
      throw Error("登录状态响应异常，请重新连接。");
    if (
      body.authenticated &&
      (typeof body.user?.name !== "string" ||
        !body.user.name ||
        !["admin", "editor", "viewer"].includes(body.user.role))
    )
      throw Error("账户信息不完整，请重新登录。");
    this.publish({
      server: body.server,
      mode: body.mode,
      phase: body.authenticated ? "authenticated" : "locked",
      user: body.user || this.state.user,
      setupRequired: !!body.setup_required,
      error: "",
      busy: false,
      ...(body.authenticated
        ? { workspaceKey: `${body.mode}:${body.user!.name}:${body.user!.role}` }
        : {}),
    });
  }
  async check() {
    const generation = ++this.generation;
    this.publish({ busy: true, error: "" });
    try {
      const response = await this.transport("/api/auth/status", {
        cache: "no-store",
        credentials: "same-origin",
      });
      const body = await response.json();
      if (!response.ok)
        throw Error(
          typeof body.detail === "string"
            ? body.detail
            : "无法读取登录状态，请检查服务连接后重试。",
        );
      if (generation === this.generation) this.accept(body);
    } catch (error) {
      if (generation === this.generation)
        this.publish({
          phase: this.state.workspaceKey ? "locked" : "error",
          busy: false,
          error: error instanceof Error ? error.message : "无法连接工作台",
        });
    }
  }
  lock() {
    if (this.state.phase !== "authenticated") return;
    if (!this.state.busy) this.generation++;
    this.publish({
      phase: "locked",
      busy: this.state.busy,
      error: "登录已过期。重新验证后可继续编辑，当前草稿仍保留。",
    });
  }
  async login(credentials: LoginCredentials) {
    if (this.state.busy) return;
    const generation = ++this.generation;
    this.publish({ busy: true, error: "" });
    try {
      const response = await this.transport("/api/auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(credentials),
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok)
        throw Error(
          typeof body.detail === "string"
            ? body.detail
            : "登录未完成，请稍后重试。",
        );
      if (generation === this.generation) this.accept(body);
    } catch (error) {
      if (generation === this.generation)
        this.publish({
          phase: "locked",
          busy: false,
          error: error instanceof Error ? error.message : "无法连接工作台",
        });
    }
  }
  async logout() {
    if (this.state.busy) return;
    const generation = ++this.generation;
    this.publish({ busy: true, error: "" });
    try {
      const session = await this.transport("/api/session", {
        cache: "no-store",
        credentials: "same-origin",
      });
      if (session.status === 401) {
        if (generation === this.generation)
          this.publish({
            phase: "locked",
            user: undefined,
            workspaceKey: undefined,
            busy: false,
          });
        return;
      }
      if (!session.ok) throw Error("无法退出登录，请稍后重试。");
      const token = (await session.json()).token;
      if (typeof token !== "string") throw Error("会话响应异常，请稍后重试。");
      const response = await this.transport("/api/auth/logout", {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-Studio-Token": token },
      });
      if (!response.ok) throw Error("退出未完成，请稍后重试。");
      if (generation === this.generation)
        this.publish({
          phase: "locked",
          user: undefined,
          workspaceKey: undefined,
          busy: false,
          error: "",
        });
    } catch (error) {
      if (generation === this.generation)
        this.publish({
          busy: false,
          error: error instanceof Error ? error.message : "退出未完成",
        });
    }
  }
}
