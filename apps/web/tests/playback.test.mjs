import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";
const { outputText } = ts.transpileModule(
  await readFile(new URL("../src/playback.ts", import.meta.url), "utf8"),
  {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  },
);
const { previewReady, mergePreviewMetadata } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
);

test("old high-bitrate compatibility files never masquerade as a low preview", () => {
  const project = { id: "source", has_video: true, preview_file: "old720.mp4" };
  assert.equal(previewReady(project, "low"), false);
  assert.equal(
    previewReady({ ...project, preview_profile: "low320-v1" }, "low"),
    true,
  );
  assert.equal(previewReady(project, "high"), false);
  assert.equal(
    previewReady({ ...project, high_preview_file: "high.mp4" }, "high"),
    true,
  );
  assert.equal(previewReady({ ...project, has_video: false }, "low"), true);
});

test("preview arrival preserves drafts, revision, permissions and model selection", () => {
  const draft = {
    id: "one",
    revision: 3,
    clips: [{ title: "未保存的歌名" }],
    detector_model_id: "chosen",
    can_edit: false,
  };
  const fresh = {
    id: "one",
    revision: 4,
    clips: [{ title: "服务器旧歌名" }],
    detector_model_id: "old",
    can_edit: true,
    preview_file: "low.mp4",
    preview_profile: "low320-v1",
    high_preview_file: "high.mp4",
    source_height: 1080,
  };
  const merged = mergePreviewMetadata(draft, fresh);
  assert.equal(merged.clips, draft.clips);
  assert.equal(merged.revision, 3);
  assert.equal(merged.detector_model_id, "chosen");
  assert.equal(merged.can_edit, false);
  assert.equal(merged.preview_file, "low.mp4");
  assert.equal(merged.high_preview_file, "high.mp4");
  assert.equal(merged.source_height, 1080);
  assert.equal(mergePreviewMetadata(draft, { ...fresh, id: "other" }), draft);
});
