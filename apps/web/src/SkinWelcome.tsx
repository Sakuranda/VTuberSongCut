import catgirl from "./assets/skins/green-eyed-catgirl.png";

export function SkinAvatar() {
  return (
    <span className="skin-avatar" aria-hidden="true">
      <img src={catgirl} alt="" width={1254} height={1254} decoding="async" />
    </span>
  );
}

export function SkinWelcome({
  decorated,
  loading,
  busy,
  onImport,
  onModels,
}: {
  decorated: boolean;
  loading: boolean;
  busy: boolean;
  onImport: () => void;
  onModels: () => void;
}) {
  return (
    <div className={`skin-welcome${decorated ? " with-character" : ""}`}>
      <div className="skin-welcome-copy">
        <div className="welcome-note" aria-hidden="true">
          ♫
        </div>
        <h3>{loading ? "正在读取工作空间…" : "导入录播，开始切歌"}</h3>
        <p>
          支持 MP4、FLV、MKV 等视频及音频。
          <br />
          可使用模型检测演唱，也可手动切歌。
        </p>
        <div className="skin-welcome-actions">
          <button
            className="button primary"
            disabled={busy || loading}
            onClick={onImport}
          >
            导入录播
          </button>
          <button className="button" disabled={busy} onClick={onModels}>
            导入已有模型
          </button>
        </div>
      </div>
      {decorated && (
        <div className="skin-welcome-art" aria-hidden="true">
          <div className="skin-art-halo" />
          <img
            src={catgirl}
            alt=""
            width={1254}
            height={1254}
            decoding="async"
          />
        </div>
      )}
    </div>
  );
}
