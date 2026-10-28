import { useRef, useState } from "react";
import {
  api,
  type Recording,
  type RecorderRoom,
  type RecorderStatus,
} from "./api";
import { usePolling } from "./usePolling";
import "./recordings.css";

const labels = {
  queued: "等待导入",
  importing: "正在导入",
  ready: "可以剪辑",
  failed: "导入失败",
};
const moment = (date: string) => new Date(date).toLocaleString();
export function RecordingsWorkspace({
  active,
  admin,
  onOpen,
}: {
  active: boolean;
  admin: boolean;
  onOpen: (id: string) => Promise<void>;
}) {
  const [status, setStatus] = useState<RecorderStatus | null>(null);
  const [rooms, setRooms] = useState<RecorderRoom[]>([]);
  const [items, setItems] = useState<Recording[]>([]);
  const [room, setRoom] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const generation = useRef(0);
  usePolling(
    async (signal) => {
      const current = ++generation.current;
      try {
        const recordings = await api.recordings(signal);
        if (!signal.aborted && current === generation.current)
          setItems(recordings);
        let state: RecorderStatus | null = null;
        let sources: RecorderRoom[] = [];
        if (admin) {
          state = await api.recorderStatus(signal);
          if (state.enabled && state.connected)
            sources = await api.recorderRooms(signal);
        }
        if (!signal.aborted && current === generation.current) {
          setStatus(state);
          setRooms(sources);
          setError("");
        }
      } catch (e) {
        if (!signal.aborted)
          setError(e instanceof Error ? e.message : "无法读取录播库");
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    },
    active,
    10000,
    `${admin}-${refresh}`,
  );
  async function act(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await action();
      setRefresh((value) => value + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作未完成，请重试");
    } finally {
      setBusy(false);
    }
  }
  const roomId = Number(room.trim());
  return (
    <section
      className="recordings-workspace"
      hidden={!active}
      aria-label="录播库工作区"
    >
      <div className="page-heading">
        <div>
          <h2>录播库</h2>
          <p>录制完成后自动入库，打开即可预览和剪辑。</p>
        </div>
        <button
          className="button"
          disabled={busy}
          onClick={() => setRefresh((v) => v + 1)}
        >
          刷新
        </button>
      </div>
      {error && (
        <div className="banner error" role="alert">
          <span>{error}</span>
        </div>
      )}
      {admin && (
        <section className="panel recording-controls">
          <h3>直播间录制</h3>
          <p role="status">{status?.message || "正在连接录播服务…"}</p>
          {status?.disk_warning && (
            <p className="banner error" role="alert">
              {status.disk_warning}
            </p>
          )}
          {status && !status.enabled && (
            <p>
              先由管理员完成录播姬连接，再添加直播间。
              <a href="/help/recording" target="_blank" rel="noreferrer">
                查看接入说明 ↗
              </a>
            </p>
          )}
          {status?.enabled && (
            <>
              <form
                className="recording-add"
                onSubmit={(e) => {
                  e.preventDefault();
                  void act(async () => {
                    await api.addRecorderRoom(roomId);
                    setRoom("");
                  });
                }}
              >
                <label className="field">
                  B 站直播间号
                  <input
                    inputMode="numeric"
                    value={room}
                    onChange={(e) => setRoom(e.target.value)}
                    placeholder="输入直播间号"
                  />
                </label>
                <button
                  className="button primary"
                  disabled={
                    busy ||
                    !status.connected ||
                    !Number.isSafeInteger(roomId) ||
                    roomId <= 0 ||
                    roomId > 2147483647
                  }
                >
                  添加并自动录制
                </button>
              </form>
              <p className="small muted">
                最多启用 {status.max_rooms}{" "}
                个录制房间；关闭自动录制后可切换房间。
              </p>
              <div className="recording-rooms">
                {rooms.map((item) => (
                  <article key={item.room_id}>
                    <div>
                      <strong>{item.name || `房间 ${item.room_id}`}</strong>
                      <p>{item.title || "等待开播"}</p>
                      <small>
                        {item.recording
                          ? "正在录制"
                          : item.streaming
                            ? "直播中"
                            : "未开播"}
                      </small>
                    </div>
                    <label className="recording-checkbox">
                      <input
                        type="checkbox"
                        checked={item.auto_record}
                        disabled={busy}
                        onChange={(e) =>
                          void act(() =>
                            api.configureRecorderRoom(
                              item.room_id,
                              e.target.checked,
                            ),
                          )
                        }
                      />
                      自动录制
                    </label>
                    <button
                      className="button"
                      disabled={busy}
                      onClick={() =>
                        void act(() =>
                          api.recorderAction(
                            item.room_id,
                            item.recording ? "stop" : "start",
                          ),
                        )
                      }
                    >
                      {item.recording ? "停止录制" : "开始录制"}
                    </button>
                  </article>
                ))}
              </div>
            </>
          )}
        </section>
      )}
      <section className="panel recording-library">
        <h3>
          已录制素材 <span className="count">{items.length}</span>
        </h3>
        {loading && <p role="status">正在读取录播…</p>}
        {!loading && !items.length && (
          <p className="recording-empty">
            还没有录播。录播姬完成一段录制后，素材会出现在这里。
          </p>
        )}
        {items.map((item) => (
          <article className="recording-item" key={item.id}>
            <div>
              <strong>
                {item.title || item.name || `房间 ${item.room_id}`}
              </strong>
              <p>
                {item.name} · {moment(item.opened_at)} ·{" "}
                {item.duration < 60
                  ? `${Math.round(item.duration)} 秒`
                  : `${Math.floor(item.duration / 60)} 分 ${Math.round(item.duration % 60)} 秒`}
              </p>
              <small>
                {labels[item.status]}
                {item.error ? ` · ${item.error}` : ""}
              </small>
            </div>
            {item.status === "ready" && item.project_id && (
              <button
                className="button primary"
                disabled={busy}
                onClick={() => void act(() => onOpen(item.project_id!))}
              >
                打开录播
              </button>
            )}
            {admin && item.status === "failed" && (
              <button
                className="button"
                disabled={busy}
                onClick={() => void act(() => api.retryRecording(item.id))}
              >
                重新导入
              </button>
            )}
          </article>
        ))}
      </section>
    </section>
  );
}
