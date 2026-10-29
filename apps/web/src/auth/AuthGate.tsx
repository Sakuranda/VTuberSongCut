import {
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { ServerSession } from "./ServerSession";
import { LoginPanel } from "./LoginPanel";
import { unsavedChanges } from "./UnsavedChanges";
import { resetApiSession } from "../api";
import { useAppearance } from "../theme";
import "./login.css";
export function AuthGate({
  children,
}: {
  children: (active: boolean, onLogout?: () => void) => ReactNode;
}) {
  useAppearance();
  const [session] = useState(() => new ServerSession());
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const workspace = useRef<HTMLDivElement>(null);
  useEffect(() => {
    void session.check();
    const expired = () => session.lock();
    window.addEventListener("songcut-auth-required", expired);
    return () => window.removeEventListener("songcut-auth-required", expired);
  }, [session]);
  const active = state.phase === "authenticated";
  useEffect(() => {
    if (!active)
      workspace.current
        ?.querySelectorAll<HTMLDialogElement>("dialog[open]")
        .forEach((dialog) => dialog.close());
  }, [active]);
  async function login(values: Parameters<ServerSession["login"]>[0]) {
    resetApiSession();
    await session.login(values);
  }
  async function logout() {
    if (
      unsavedChanges.pending &&
      !window.confirm("仍有未保存修改。退出登录并丢弃这些修改？")
    )
      return;
    await session.logout();
    resetApiSession();
  }
  return (
    <>
      {state.workspaceKey && (
        <div
          ref={workspace}
          hidden={!active}
          inert={!active}
          className="authenticated-workspace"
          key={state.workspaceKey}
        >
          {children(
            active,
            state.server && state.mode === "totp"
              ? () => void logout()
              : undefined,
          )}
        </div>
      )}
      {!active && (
        <LoginPanel
          state={state}
          onLogin={login}
          onRetry={() => void session.check()}
        />
      )}
      {active && state.error && (
        <div className="session-error" role="alert">
          {state.error}
        </div>
      )}
    </>
  );
}
