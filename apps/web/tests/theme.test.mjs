import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";
// The controller is tested in a browser-shaped environment, without rendering hooks.
const { outputText } = ts.transpileModule(
  await readFile(new URL("../src/theme.ts", import.meta.url), "utf8"),
  {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  },
);
const source = outputText.replace(
  'import { useSyncExternalStore } from "react";',
  'const useSyncExternalStore = () => { throw new Error("Hook cannot be used in controller tests"); };',
);
const {
  createAppearanceController,
  parseAppearance,
  APPEARANCE_STORAGE_KEY,
  DEFAULT_APPEARANCE,
} = await import(
  `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`
);

function fixture(raw = null, blockedStorage = false) {
  const events = new EventTarget();
  const media = new EventTarget();
  media.matches = false;
  const root = { dataset: {}, style: {} };
  let stored = raw;
  const storage = {
    getItem: () => stored,
    setItem: (_, value) => {
      stored = value;
    },
  };
  const env = {
    matchMedia: () => media,
    addEventListener: events.addEventListener.bind(events),
    removeEventListener: events.removeEventListener.bind(events),
    get localStorage() {
      if (blockedStorage) throw new Error("Storage disabled");
      return storage;
    },
  };
  const controller = createAppearanceController(env, root);
  return {
    controller,
    root,
    get stored() {
      return stored;
    },
    system(dark) {
      media.matches = dark;
      media.dispatchEvent(new Event("change"));
    },
    external(value, key = APPEARANCE_STORAGE_KEY) {
      const event = new Event("storage");
      Object.assign(event, { key, newValue: value, storageArea: storage });
      events.dispatchEvent(event);
    },
  };
}

test("damaged, old and unsupported settings recover safely without discarding valid preferences", () => {
  for (const raw of [
    null,
    "{",
    "null",
    "[]",
    '"plain"',
    '{"version":2,"palette":"rose"}',
  ])
    assert.deepEqual(parseAppearance(raw), DEFAULT_APPEARANCE);
  assert.deepEqual(
    parseAppearance(
      '{"version":1,"palette":"rose","mode":"sepia","skin":"plain"}',
    ),
    { version: 1, palette: "rose", mode: "system", skin: "plain" },
  );
  assert.deepEqual(
    parseAppearance(
      '{"version":1,"palette":["rose"],"mode":["dark"],"skin":null}',
    ),
    DEFAULT_APPEARANCE,
  );
  const f = fixture("{broken");
  assert.equal(f.root.dataset.palette, "jade");
  f.controller.updateAppearance({
    mode: "dark",
    skin: "plain",
    palette: "violet",
  });
  assert.equal(JSON.parse(f.stored).palette, "violet");
  assert.equal(f.root.style.colorScheme, "dark");
  f.controller.dispose();
});

test("blocked storage still permits selection and reset during this session", () => {
  const f = fixture(null, true);
  f.controller.updateAppearance({ palette: "rose", mode: "dark" });
  assert.equal(f.controller.getSnapshot().appearance.palette, "rose");
  assert.equal(f.root.dataset.mode, "dark");
  f.controller.resetAppearance();
  assert.deepEqual(f.controller.getSnapshot().appearance, DEFAULT_APPEARANCE);
  f.controller.dispose();
});

test("system changes update only automatic mode; explicit modes remain stable and listeners clean up", () => {
  const f = fixture();
  let notifications = 0;
  const stop = f.controller.subscribe(() => notifications++);
  f.system(true);
  assert.equal(f.controller.getSnapshot().resolvedMode, "dark");
  f.controller.updateAppearance({ mode: "light" });
  const snapshot = f.controller.getSnapshot();
  f.system(false);
  f.system(true);
  assert.equal(f.controller.getSnapshot(), snapshot);
  f.controller.updateAppearance({ mode: "system" });
  assert.equal(f.controller.getSnapshot().resolvedMode, "dark");
  stop();
  const count = notifications;
  f.system(false);
  assert.equal(notifications, count);
  f.controller.dispose();
  const final = f.controller.getSnapshot();
  f.system(true);
  assert.equal(f.controller.getSnapshot(), final);
});

test("another tab synchronizes settings and clear-storage restores defaults", () => {
  const f = fixture();
  f.external(
    JSON.stringify({
      version: 1,
      skin: "plain",
      palette: "classic",
      mode: "dark",
    }),
  );
  assert.equal(f.root.dataset.skin, "plain");
  assert.equal(f.root.dataset.palette, "classic");
  assert.equal(f.root.dataset.mode, "dark");
  const snapshot = f.controller.getSnapshot();
  f.external("{}", "unrelated.setting");
  assert.equal(f.controller.getSnapshot(), snapshot);
  f.external(null, null);
  assert.deepEqual(f.controller.getSnapshot().appearance, DEFAULT_APPEARANCE);
  f.controller.dispose();
});

const css = await readFile(
  new URL("../src/themes.css", import.meta.url),
  "utf8",
);
const rules = [
  ...css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/([^{}]+)\{([^{}]*)\}/g),
].map(([, selector, body]) => ({
  selector: selector.trim(),
  tokens: Object.fromEntries(
    [...body.matchAll(/(--[\w-]+):\s*(#[\da-f]{6});/gi)].map(
      ([, key, value]) => [key, value],
    ),
  ),
}));
function contrast(a, b) {
  const luminance = (hex) =>
    [1, 3, 5]
      .map((offset) => parseInt(hex.slice(offset, offset + 2), 16) / 255)
      .map((n) => (n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4))
      .reduce((sum, value, i) => sum + value * [0.2126, 0.7152, 0.0722][i], 0);
  const values = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (values[0] + 0.05) / (values[1] + 0.05);
}
test("every palette in both modes keeps readable text, links, buttons and status messages", () => {
  for (const mode of ["light", "dark"])
    for (const palette of ["jade", "violet", "rose", "classic"]) {
      const tokens = {};
      for (const { selector, tokens: values } of rules) {
        if (!selector.startsWith(":root")) continue;
        const wantedPalette = selector.match(/data-palette="([^"]+)"/)?.[1];
        const wantedMode = selector.match(/data-mode="([^"]+)"/)?.[1];
        if (
          (wantedPalette && wantedPalette !== palette) ||
          (wantedMode && wantedMode !== mode)
        )
          continue;
        Object.assign(tokens, values);
      }
      for (const [foreground, background] of [
        ["--ink", "--surface"],
        ["--muted", "--surface"],
        ["--muted", "--page"],
        ["--muted", "--surface-soft"],
        ["--muted", "--accent-soft"],
        ["--accent", "--surface"],
        ["--accent", "--accent-soft"],
        ["--on-accent", "--accent-solid"],
        ["--on-accent", "--accent-hover"],
        ["--error", "--error-soft"],
        ["--success", "--success-soft"],
        ["--warning", "--warning-soft"],
      ]) {
        const ratio = contrast(tokens[foreground], tokens[background]);
        assert.ok(
          ratio >= 4.5,
          `${palette}/${mode} ${foreground} on ${background}: ${ratio.toFixed(2)} < 4.5`,
        );
      }
    }
});
