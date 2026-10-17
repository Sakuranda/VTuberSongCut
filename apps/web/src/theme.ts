import { useSyncExternalStore } from "react";

export type Skin = "catgirl" | "plain";
export type Palette = "jade" | "violet" | "rose" | "classic";
export type AppearanceMode = "light" | "dark" | "system";
export type ResolvedMode = "light" | "dark";
export type Appearance = Readonly<{
  version: 1;
  skin: Skin;
  palette: Palette;
  mode: AppearanceMode;
}>;
export type AppearancePatch = Partial<Omit<Appearance, "version">>;
export const APPEARANCE_STORAGE_KEY = "vtuber-songcut.appearance";
export const DEFAULT_APPEARANCE: Appearance = Object.freeze({
  version: 1,
  skin: "catgirl",
  palette: "jade",
  mode: "system",
});

export function parseAppearance(raw: string | null): Appearance {
  try {
    const value: unknown = raw === null ? null : JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value))
      return DEFAULT_APPEARANCE;
    const v = value as Record<string, unknown>;
    if (v.version !== 1) return DEFAULT_APPEARANCE;
    return Object.freeze({
      version: 1,
      skin:
        v.skin === "plain" || v.skin === "catgirl"
          ? v.skin
          : DEFAULT_APPEARANCE.skin,
      palette:
        typeof v.palette === "string" &&
        ["jade", "violet", "rose", "classic"].includes(v.palette)
          ? (v.palette as Palette)
          : DEFAULT_APPEARANCE.palette,
      mode:
        typeof v.mode === "string" &&
        ["light", "dark", "system"].includes(v.mode)
          ? (v.mode as AppearanceMode)
          : DEFAULT_APPEARANCE.mode,
    });
  } catch {
    return DEFAULT_APPEARANCE;
  }
}

type AppearanceEnvironment = Pick<
  Window,
  "matchMedia" | "addEventListener" | "removeEventListener" | "localStorage"
>;
export function createAppearanceController(
  environment: AppearanceEnvironment,
  root: HTMLElement,
) {
  const media = environment.matchMedia("(prefers-color-scheme: dark)");
  const listeners = new Set<() => void>();
  let appearance = DEFAULT_APPEARANCE;
  try {
    appearance = parseAppearance(
      environment.localStorage.getItem(APPEARANCE_STORAGE_KEY),
    );
  } catch {
    /* Storage may be disabled. */
  }
  const resolve = (): ResolvedMode =>
    appearance.mode === "system"
      ? media.matches
        ? "dark"
        : "light"
      : appearance.mode;
  let snapshot = { appearance, resolvedMode: resolve() };
  const publish = () => {
    snapshot = { appearance, resolvedMode: resolve() };
    root.dataset.skin = appearance.skin;
    root.dataset.palette = appearance.palette;
    root.dataset.mode = snapshot.resolvedMode;
    root.style.colorScheme = snapshot.resolvedMode;
    listeners.forEach((listener) => listener());
  };
  const onSystemChange = () => {
    if (appearance.mode === "system") publish();
  };
  const onStorage = (event: StorageEvent) => {
    if (event.key !== null && event.key !== APPEARANCE_STORAGE_KEY) return;
    if (event.storageArea) {
      try {
        if (event.storageArea !== environment.localStorage) return;
      } catch {
        return;
      }
    }
    appearance = parseAppearance(event.newValue);
    publish();
  };
  media.addEventListener("change", onSystemChange);
  environment.addEventListener("storage", onStorage);
  publish();
  const updateAppearance = (patch: AppearancePatch) => {
    appearance = parseAppearance(
      JSON.stringify({ ...appearance, ...patch, version: 1 }),
    );
    try {
      environment.localStorage.setItem(
        APPEARANCE_STORAGE_KEY,
        JSON.stringify(appearance),
      );
    } catch {
      /* Keep this session usable even without storage. */
    }
    publish();
  };
  return {
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    getSnapshot: () => snapshot,
    updateAppearance,
    resetAppearance: () => updateAppearance(DEFAULT_APPEARANCE),
    dispose() {
      media.removeEventListener("change", onSystemChange);
      environment.removeEventListener("storage", onStorage);
      listeners.clear();
    },
  };
}

let controller: ReturnType<typeof createAppearanceController> | undefined;
export function useAppearance() {
  controller ??= createAppearanceController(window, document.documentElement);
  const state = useSyncExternalStore(
    controller.subscribe,
    controller.getSnapshot,
  );
  return {
    ...state,
    updateAppearance: controller.updateAppearance,
    resetAppearance: controller.resetAppearance,
  };
}
