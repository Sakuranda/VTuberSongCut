import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const { outputText } = ts.transpileModule(
  await readFile(
    new URL("../src/TrainingWorkspace.tsx", import.meta.url),
    "utf8",
  ),
  {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
    },
  },
);
const tick = () => new Promise(setImmediate);
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
};
const dataset = (id) => ({
  id,
  name: id,
  target_name: "主播",
  revision: 1,
  samples: [
    {
      id: `${id}-sample`,
      name: "录播",
      split: "train",
      reviewed: false,
      regions: [],
      duration_ms: 1000,
    },
  ],
});
const text = (node) =>
  Array.isArray(node)
    ? node.map(text).join("")
    : node && typeof node === "object"
      ? text(node.props?.children)
      : String(node ?? "");
function find(node, predicate) {
  if (Array.isArray(node))
    return node.map((child) => find(child, predicate)).find(Boolean);
  if (!node || typeof node !== "object") return undefined;
  return predicate(node) ? node : find(node.props?.children, predicate);
}

// Exercise the real component's hooks, effects, and event handlers without a
// DOM dependency. Child components are inert, so only workspace behavior runs.
function workspace(api) {
  const slots = [];
  let cursor = 0;
  let changed = true;
  let effects = [];
  let tree;
  let props = {
    active: true,
    catalog: null,
    onModelsChanged() {},
    onUseModel() {},
    modelManager: null,
  };
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { value: initial };
      return [
        slots[index].value,
        (next) => {
          const value =
            typeof next === "function" ? next(slots[index].value) : next;
          if (!Object.is(value, slots[index].value)) {
            slots[index].value = value;
            changed = true;
          }
        },
      ];
    },
    useRef(initial) {
      const index = cursor++;
      slots[index] ??= { current: initial };
      return slots[index];
    },
    useEffect(effect, deps) {
      const index = cursor++;
      const previous = slots[index];
      if (
        !previous ||
        !deps ||
        deps.some((dep, i) => !Object.is(dep, previous.deps?.[i]))
      ) {
        effects.push(() => {
          previous?.cleanup?.();
          slots[index] = { deps, cleanup: effect() };
        });
      }
    },
  };
  const exports = {};
  const jsx = (type, props) => ({ type, props });
  const modules = {
    react: hooks,
    "react/jsx-runtime": { jsx, jsxs: jsx, Fragment: "Fragment" },
    "./api": { api },
    "./TrainingAnnotation": { TrainingAnnotation: "TrainingAnnotation" },
    "./UploadFeedback": { UploadFeedback: "UploadFeedback" },
    "./usePolling": { usePolling() {} },
    "./auth/UnsavedChanges": { unsavedChanges: { set() {} } },
  };
  new Function("require", "exports", "window", outputText)(
    (name) => {
      assert.ok(name in modules, `unexpected module ${name}`);
      return modules[name];
    },
    exports,
    { addEventListener() {}, removeEventListener() {}, confirm: () => true },
  );
  return {
    render(next = {}) {
      props = { ...props, ...next };
      do {
        changed = false;
        cursor = 0;
        tree = exports.TrainingWorkspace(props);
        const pending = effects;
        effects = [];
        pending.forEach((effect) => effect());
      } while (changed);
      return tree;
    },
    button(label) {
      return find(
        tree,
        (node) => node.type === "button" && text(node) === label,
      );
    },
    annotation() {
      return find(tree, (node) => node.type === "TrainingAnnotation");
    },
    find(predicate) {
      return find(tree, predicate);
    },
  };
}

test("failed startup offers a working retry that loads projects without reloading the page", async () => {
  const retry = deferred();
  let reads = 0;
  const view = workspace({
    trainingDatasets: () =>
      ++reads === 1 ? Promise.reject(new Error("服务暂不可用")) : retry.promise,
  });
  view.render();
  await tick();
  view.render();
  assert.ok(view.button("重新读取项目"));
  view.button("重新读取项目").props.onClick();
  view.render();
  assert.equal(reads, 2);
  retry.resolve([dataset("recovered")]);
  await tick();
  view.render();
  assert.equal(view.annotation().props.dataset.id, "recovered");
});

test("a recovered list preserves a newly created project and unsaved annotations", async () => {
  const retry = deferred();
  let reads = 0;
  const created = dataset("created-after-failure");
  const view = workspace({
    trainingDatasets: () =>
      ++reads === 1 ? Promise.reject(new Error("offline")) : retry.promise,
    createTrainingDataset: async () => created,
  });
  view.render();
  await tick();
  view.render();
  view
    .find(
      (node) =>
        node.type === "input" && node.props.placeholder === "要识别谁的演唱？",
    )
    .props.onChange({ target: { value: "主播" } });
  view.render();
  view.button("创建训练项目").props.onClick();
  await tick();
  view.render();
  assert.equal(view.annotation().props.dataset.id, created.id);
  view.render({ active: false });
  view.render({ active: true });
  assert.equal(reads, 2);
  const draft = {
    ...created.samples[0],
    reviewed: true,
    regions: [{ start_ms: 100, end_ms: 800, label: "target" }],
  };
  view.annotation().props.onChange(draft);
  view.render();
  retry.resolve([dataset("older-server-selection")]);
  await tick();
  view.render();
  assert.equal(view.annotation().props.dataset.id, created.id);
  assert.deepEqual(view.annotation().props.sample, draft);
  assert.equal(view.button("保存标注").props.disabled, false);
  assert.ok(
    view.find(
      (node) => node.type === "option" && node.props.value === created.id,
    ),
  );
});

test("late responses from an inactive load cannot replace the next selection", async () => {
  const first = deferred();
  const second = deferred();
  let reads = 0;
  const view = workspace({
    trainingDatasets: () => (++reads === 1 ? first.promise : second.promise),
  });
  view.render();
  view.render({ active: false });
  view.render({ active: true });
  second.resolve([dataset("current")]);
  await tick();
  view.render();
  first.resolve([dataset("stale")]);
  await tick();
  view.render();
  assert.equal(view.annotation().props.dataset.id, "current");
});

test("retry stays disabled when a background load fails during project creation", async () => {
  const creation = deferred();
  let reads = 0;
  const view = workspace({
    trainingDatasets: () => {
      reads++;
      return Promise.reject(new Error("offline"));
    },
    createTrainingDataset: () => creation.promise,
  });
  view.render();
  await tick();
  view.render();
  view
    .find(
      (node) =>
        node.type === "input" && node.props.placeholder === "要识别谁的演唱？",
    )
    .props.onChange({ target: { value: "主播" } });
  view.render();
  view.button("创建训练项目").props.onClick();
  view.render();
  view.render({ active: false });
  view.render({ active: true });
  await tick();
  view.render();
  assert.equal(reads, 2);
  assert.equal(view.button("重新读取项目").props.disabled, true);
  creation.resolve(dataset("created"));
  await tick();
  view.render();
  assert.equal(view.annotation().props.dataset.id, "created");
});
