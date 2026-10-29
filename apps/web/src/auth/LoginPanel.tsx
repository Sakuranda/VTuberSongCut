import { useState } from "react";
import type { LoginCredentials, SessionSnapshot } from "./ServerSession";
import catgirl from "../assets/skins/green-eyed-catgirl.png";
export function LoginPanel({
  state,
  onLogin,
  onRetry,
}: {
  state: SessionSnapshot;
  onLogin: (values: LoginCredentials) => Promise<void>;
  onRetry: () => void;
}) {
  const [username, setUsername] = useState(state.user?.name || "");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [visible, setVisible] = useState(false);
  const waiting = state.phase === "checking";
  return (
    <main className="login-page">
      <section className="login-card" aria-labelledby="login-title">
        <div className="login-brand">
          <span>拾歌</span>
          <small>VTuber SongCut</small>
        </div>
        <div className="login-art">
          <img src={catgirl} alt="" width="1254" height="1254" />
        </div>
        <h1 id="login-title">{waiting ? "正在连接工作台" : "登录拾歌"}</h1>
        <p className="login-intro">
          账号、密码与六位动态验证码，共同保护你的录播和剪辑。
        </p>
        {state.phase === "locked" && state.mode === "totp" && (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const values = { username: username.trim(), password, code };
              setPassword("");
              setCode("");
              setVisible(false);
              void onLogin(values);
            }}
          >
            <label>
              账号
              <input
                name="username"
                autoComplete="username"
                value={username}
                maxLength={200}
                disabled={state.busy}
                onChange={(e) => setUsername(e.target.value)}
                required
                autoFocus
              />
            </label>
            <label>
              密码
              <div className="password-field">
                <input
                  name="password"
                  type={visible ? "text" : "password"}
                  autoComplete="current-password"
                  value={password}
                  maxLength={1024}
                  disabled={state.busy}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
                <button
                  type="button"
                  aria-label={visible ? "隐藏密码" : "显示密码"}
                  aria-pressed={visible}
                  onClick={() => setVisible(!visible)}
                >
                  {visible ? "隐藏" : "显示"}
                </button>
              </div>
            </label>
            <label>
              六位动态验证码
              <input
                name="code"
                className="otp-field"
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]{6}"
                maxLength={6}
                value={code}
                disabled={state.busy}
                onChange={(e) =>
                  setCode(e.target.value.replace(/\D/g, "").slice(0, 6))
                }
                placeholder="000000"
                required
              />
            </label>
            <p className="login-help">
              填写身份验证器当前显示的六位数字。已使用的验证码请等待下一组。
            </p>
            <button
              className="button primary login-submit"
              type="submit"
              disabled={
                state.busy ||
                state.setupRequired ||
                !username.trim() ||
                !password ||
                code.length !== 6
              }
            >
              {state.busy ? "正在验证…" : "登录工作台"}
            </button>
          </form>
        )}
        {state.setupRequired && (
          <p className="login-notice" role="status">
            管理员还未完成身份验证器绑定，请先完成服务器初始化。
          </p>
        )}
        {state.mode === "basic" && (
          <p className="login-notice">
            此服务器使用兼容登录，请重新打开页面登录。
          </p>
        )}
        {state.error && (
          <p className="login-error" role="alert">
            {state.error}
          </p>
        )}
        {(state.phase === "error" ||
          state.setupRequired ||
          state.mode === "basic") && (
          <button
            className="button login-retry"
            disabled={state.busy}
            onClick={onRetry}
          >
            重新检查连接
          </button>
        )}
      </section>
      <p className="login-footer">登录凭据不会保存到网页的本地存储。</p>
    </main>
  );
}
