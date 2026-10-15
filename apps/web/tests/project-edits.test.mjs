import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

async function sourceModule(path) {
  const source = await readFile(new URL(path, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  });
  return import(
    `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
  );
}
const { api, projectEdits } = await sourceModule("../src/api.ts");
const { normalizeProject } = await sourceModule("../src/editing.ts");
const legacy = { id: "recording", revision: 7, clips: [], duration_ms: 30000 };

test("old projects require an explicit model and default to automatic device", () => {
  const project = normalizeProject(legacy);
  assert.equal(project.detector_model_id, null);
  assert.equal(project.detector_device, "auto");
  assert.equal(projectEdits(project).detector_model_id, null);
});

test("model and device changes dirty the saved project while preview updates do not", () => {
  const project = normalizeProject(legacy);
  const snapshot = JSON.stringify(projectEdits(project));
  assert.notEqual(
    JSON.stringify(
      projectEdits({ ...project, detector_model_id: "trained-model" }),
    ),
    snapshot,
  );
  assert.notEqual(
    JSON.stringify(projectEdits({ ...project, detector_device: "mps" })),
    snapshot,
  );
  assert.equal(
    JSON.stringify(
      projectEdits({ ...project, preview_file: "preview.mp4", revision: 8 }),
    ),
    snapshot,
  );
  const edited = {
    ...project,
    clips: [{ id: "draft-clip", title: "未保存标题" }],
    detector_model_id: "trained-model",
    detector_device: "mps",
  };
  assert.equal(projectEdits(edited).clips[0].title, "未保存标题");
});

test("save sends the selected model and device with the same revision and clip draft", async () => {
  const originalFetch = globalThis.fetch;
  const project = normalizeProject({
    ...legacy,
    clips: [{ id: "draft-clip", title: "草稿" }],
    detector_model_id: "trained-model",
    detector_device: "cpu",
  });
  const requests = [];
  globalThis.fetch = async (url, init) => {
    requests.push({ url, init });
    return new Response(
      JSON.stringify(
        url === "/api/session" ? { token: "local-session" } : project,
      ),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  };
  try {
    const restored = await api.save(project);
    const request = requests.find(
      (item) => item.url === "/api/projects/recording",
    );
    assert.equal(request.init.method, "PUT");
    assert.equal(
      new Headers(request.init.headers).get("X-Studio-Token"),
      "local-session",
    );
    assert.deepEqual(JSON.parse(request.init.body), {
      revision: 7,
      clips: project.clips,
      detector_model_id: "trained-model",
      detector_device: "cpu",
    });
    assert.deepEqual(
      projectEdits(normalizeProject(restored)),
      projectEdits(project),
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
});
