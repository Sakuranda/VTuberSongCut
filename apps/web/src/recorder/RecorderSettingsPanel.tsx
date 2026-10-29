import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { request } from "../api";
import { unsavedChanges } from "../auth/UnsavedChanges";
import { RecorderSettingsService } from "./RecorderSettingsService";
import "./recorder-settings.css";

export function RecorderSettingsPanel({ active }: { active: boolean }) {
  const tracking = useRef(false);
  const [controller] = useState(
    () =>
      new RecorderSettingsService(request, (dirty) => {
        if (tracking.current) unsavedChanges.set("recorder-settings", dirty);
      }),
  );
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
  );
  useEffect(() => {
    controller.setActive(active);
    return () => controller.suspend();
  }, [active, controller]);
  useEffect(() => {
    tracking.current = true;
    unsavedChanges.set("recorder-settings", controller.getSnapshot().dirty);
    return () => {
      tracking.current = false;
      unsavedChanges.set("recorder-settings", false);
    };
  }, [controller]);
  useEffect(() => {
    if (!state.dirty) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [state.dirty]);
  const reply = state.reply,
    draft = state.draft;
  const disabled = state.loading || state.saving;
  return (
    <section className="panel recorder-settings" aria-label="录播姬设置">
      <div className="recorder-settings-heading">
        <div>
          <h3>录播姬设置</h3>
          <p>在这里调整原片录制、分段和弹幕保存。</p>
        </div>
        <button
          type="button"
          className="button"
          aria-expanded={state.open}
          onClick={() =>
            state.open ? controller.close() : void controller.open()
          }
        >
          {state.open ? "收起" : "展开设置"}
        </button>
      </div>
      {!state.open && state.dirty && (
        <p className="recorder-settings-note">有未保存的设置草稿。</p>
      )}
      {state.open && (
        <>
          {state.loading && <p role="status">正在读取设置…</p>}
          {state.error && (
            <div className="banner error" role="alert">
              <span>{state.error}</span>
            </div>
          )}
          {state.conflict && (
            <p className="recorder-settings-note">
              其他页面可能修改过设置。你的普通设置草稿仍在；重新读取会替换它。
            </p>
          )}
          {reply && !reply.available && (
            <p role="status" className="recorder-settings-note">
              {reply.message}
            </p>
          )}
          {reply?.available && draft && (
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void controller.save();
              }}
            >
              <fieldset
                disabled={disabled}
                className="recorder-settings-fields"
              >
                <div className="recorder-settings-row">
                  <label className="field recorder-quality">
                    录制画质
                    <select
                      value={draft.recording_quality}
                      onChange={(e) =>
                        controller.update("recording_quality", e.target.value)
                      }
                    >
                      {!reply.quality_options.some(
                        (q) => q.value === draft.recording_quality,
                      ) && (
                        <option value={draft.recording_quality}>
                          当前自定义配置
                        </option>
                      )}
                      {reply.quality_options.map((q) => (
                        <option value={q.value} key={q.value}>
                          {q.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field">
                    文件分段
                    <select
                      value={draft.split_mode}
                      onChange={(e) =>
                        controller.update(
                          "split_mode",
                          e.target.value as "disabled" | "time" | "size",
                        )
                      }
                    >
                      <option value="disabled">不自动分段</option>
                      <option value="time">按时长</option>
                      <option value="size">按文件大小</option>
                    </select>
                  </label>
                  {draft.split_mode !== "disabled" && (
                    <label className="field recorder-split">
                      {draft.split_mode === "time"
                        ? "每段时长（分钟）"
                        : "每段大小（MiB）"}
                      <input
                        type="number"
                        min={draft.split_mode === "time" ? 1 : 16}
                        max={draft.split_mode === "time" ? 1440 : 65536}
                        step={1}
                        value={
                          Number.isFinite(draft.split_value)
                            ? draft.split_value
                            : ""
                        }
                        onChange={(e) =>
                          controller.update(
                            "split_value",
                            e.target.valueAsNumber,
                          )
                        }
                      />
                    </label>
                  )}
                </div>
                <div className="recorder-checkboxes">
                  <label>
                    <input
                      type="checkbox"
                      checked={draft.split_by_title}
                      onChange={(e) =>
                        controller.update("split_by_title", e.target.checked)
                      }
                    />
                    标题变化时分段
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={draft.record_danmaku}
                      onChange={(e) =>
                        controller.update("record_danmaku", e.target.checked)
                      }
                    />
                    保存弹幕（XML）
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={draft.record_danmaku_super_chat}
                      disabled={!draft.record_danmaku}
                      onChange={(e) =>
                        controller.update(
                          "record_danmaku_super_chat",
                          e.target.checked,
                        )
                      }
                    />
                    醒目留言
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={draft.record_danmaku_gift}
                      disabled={!draft.record_danmaku}
                      onChange={(e) =>
                        controller.update(
                          "record_danmaku_gift",
                          e.target.checked,
                        )
                      }
                    />
                    礼物
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={draft.record_danmaku_guard}
                      disabled={!draft.record_danmaku}
                      onChange={(e) =>
                        controller.update(
                          "record_danmaku_guard",
                          e.target.checked,
                        )
                      }
                    />
                    上舰信息
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={draft.save_cover}
                      onChange={(e) =>
                        controller.update("save_cover", e.target.checked)
                      }
                    />
                    保存直播封面
                  </label>
                </div>
                <details className="recorder-advanced">
                  <summary>连接与登录设置</summary>
                  <div className="recorder-settings-row recorder-timing">
                    <label className="field">
                      断线重试间隔（秒）
                      <input
                        type="number"
                        min={1}
                        max={120}
                        step={0.001}
                        value={
                          Number.isFinite(draft.retry_seconds)
                            ? draft.retry_seconds
                            : ""
                        }
                        onChange={(e) =>
                          controller.update(
                            "retry_seconds",
                            e.target.valueAsNumber,
                          )
                        }
                      />
                    </label>
                    <label className="field">
                      开播检查间隔（秒）
                      <input
                        type="number"
                        min={30}
                        max={3600}
                        step={1}
                        value={
                          Number.isFinite(draft.check_interval_seconds)
                            ? draft.check_interval_seconds
                            : ""
                        }
                        onChange={(e) =>
                          controller.update(
                            "check_interval_seconds",
                            e.target.valueAsNumber,
                          )
                        }
                      />
                    </label>
                  </div>
                  <div className="recorder-cookie-heading">
                    <strong>B站录制凭据</strong>
                    <span>
                      {reply.cookie_configured ? "已配置，不回显" : "尚未配置"}
                    </span>
                  </div>
                  <label className="field">
                    替换B站Cookie
                    <input
                      type="password"
                      autoComplete="off"
                      spellCheck={false}
                      maxLength={16384}
                      disabled={state.clearCookie}
                      value={state.cookie}
                      onChange={(e) => controller.setCookie(e.target.value)}
                      placeholder="留空保留现有Cookie"
                    />
                  </label>
                  <label className="recorder-clear-cookie">
                    <input
                      type="checkbox"
                      checked={state.clearCookie}
                      onChange={(e) =>
                        controller.setClearCookie(e.target.checked)
                      }
                    />
                    清除已保存的B站Cookie
                  </label>
                  <p className="recorder-settings-note">
                    Cookie仅提交给服务器上的录播姬，不存入浏览器。收起设置或离开此工作区时，尚未提交的输入会清除。
                  </p>
                </details>
              </fieldset>
              <p className="recorder-settings-note">{reply.notice}</p>
              <div className="recorder-settings-actions">
                <button
                  type="button"
                  className="button"
                  disabled={disabled}
                  onClick={() => void controller.reload()}
                >
                  {state.dirty ? "重新读取（替换草稿）" : "重新读取"}
                </button>
                <button
                  className="button primary"
                  disabled={disabled || !state.dirty || state.conflict}
                >
                  {state.saving ? "正在保存…" : "保存录制设置"}
                </button>
              </div>
            </form>
          )}
          {!reply?.available && (
            <button
              type="button"
              className="button"
              disabled={disabled}
              onClick={() => void controller.reload()}
            >
              重新读取
            </button>
          )}
          {state.notice && (
            <p role="status" className="recorder-settings-note">
              {state.notice}
            </p>
          )}
        </>
      )}
    </section>
  );
}
