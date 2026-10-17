import { useEffect, useId, useRef } from "react";
import type {
  Appearance,
  AppearancePatch,
  Palette,
  ResolvedMode,
} from "./theme";
import catgirl from "./assets/skins/green-eyed-catgirl.png";

const palettes: { value: Palette; label: string; color: string }[] = [
  { value: "jade", label: "翡翠绿", color: "#237565" },
  { value: "violet", label: "蝴蝶紫", color: "#7950a6" },
  { value: "rose", label: "缎带红", color: "#a64160" },
  { value: "classic", label: "经典蓝", color: "#4264cc" },
];

export function AppearanceSettings({
  open,
  onClose,
  appearance,
  resolvedMode,
  onChange,
  onReset,
}: {
  open: boolean;
  onClose: () => void;
  appearance: Appearance;
  resolvedMode: ResolvedMode;
  onChange: (patch: AppearancePatch) => void;
  onReset: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const helpId = useId();
  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    if (open && !node.open) node.showModal();
    if (!open && node.open) node.close();
  }, [open]);
  return (
    <dialog
      ref={dialog}
      className="appearance-dialog"
      aria-labelledby={titleId}
      aria-describedby={helpId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClose={() => {
        if (open) onClose();
      }}
    >
      <div className="appearance-heading">
        <div>
          <h2 id={titleId}>外观设置</h2>
        </div>
        <button
          type="button"
          className="button appearance-close"
          onClick={onClose}
          aria-label="关闭外观设置"
        >
          ×
        </button>
      </div>
      <div className="appearance-body">
        <p id={helpId} className="appearance-description">
          选择后立即生效，自动记住这台设备上的偏好。
        </p>
        <fieldset className="appearance-group">
          <legend>皮肤</legend>
          <div className="appearance-skins">
            <label
              className={`appearance-skin ${appearance.skin === "catgirl" ? "selected" : ""}`}
            >
              <input
                type="radio"
                name="appearance-skin"
                value="catgirl"
                checked={appearance.skin === "catgirl"}
                onChange={() => onChange({ skin: "catgirl" })}
              />
              <div className="appearance-skin-art">
                {open && (
                  <img
                    src={catgirl}
                    alt=""
                    width={1254}
                    height={1254}
                    loading="lazy"
                    decoding="async"
                  />
                )}
              </div>
              <strong>猫娘相伴</strong>
              <span>绿瞳、双缎带，陪你整理歌声</span>
            </label>
            <label
              className={`appearance-skin ${appearance.skin === "plain" ? "selected" : ""}`}
            >
              <input
                type="radio"
                name="appearance-skin"
                value="plain"
                checked={appearance.skin === "plain"}
                onChange={() => onChange({ skin: "plain" })}
              />
              <div className="appearance-plain-art" aria-hidden="true">
                <i />
                <i />
                <i />
                <i />
                <i />
                <i />
                <i />
              </div>
              <strong>简洁工作台</strong>
              <span>收起角色插画，专注每一段声音</span>
            </label>
          </div>
        </fieldset>
        <fieldset className="appearance-group">
          <legend>主题配色</legend>
          <div className="appearance-palettes">
            {palettes.map((palette) => (
              <label key={palette.value} className="appearance-palette">
                <input
                  type="radio"
                  name="appearance-palette"
                  value={palette.value}
                  checked={appearance.palette === palette.value}
                  onChange={() => onChange({ palette: palette.value })}
                />
                <span
                  className="appearance-swatch"
                  style={{ backgroundColor: palette.color }}
                  aria-hidden="true"
                />
                <span>{palette.label}</span>
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset className="appearance-group">
          <legend>明暗模式</legend>
          <div className="appearance-modes">
            {(
              [
                { value: "light", label: "浅色" },
                { value: "dark", label: "深色" },
                { value: "system", label: "跟随系统" },
              ] as const
            ).map((mode) => (
              <label key={mode.value}>
                <input
                  type="radio"
                  name="appearance-mode"
                  value={mode.value}
                  checked={appearance.mode === mode.value}
                  onChange={() => onChange({ mode: mode.value })}
                />
                <span>{mode.label}</span>
              </label>
            ))}
          </div>
          {appearance.mode === "system" && (
            <p className="appearance-system-note">
              当前为{resolvedMode === "dark" ? "深色" : "浅色"}
              ，会随系统自动切换。
            </p>
          )}
        </fieldset>
      </div>
      <div className="appearance-footer">
        <button type="button" className="button" onClick={onReset}>
          恢复默认
        </button>
        <button type="button" className="button primary" onClick={onClose}>
          完成
        </button>
      </div>
    </dialog>
  );
}
