import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";
const { outputText } = ts.transpileModule(
  await readFile(
    new URL("../src/recorder/RecorderSettingsService.ts", import.meta.url),
    "utf8",
  ),
  {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  },
);
const { RecorderSettingsService } = await import(
  "data:text/javascript;base64," + Buffer.from(outputText).toString("base64")
);
const preferences = () => ({
  recording_quality: "avc10000,hevc10000",
  split_mode: "disabled",
  split_value: 100,
  split_by_title: false,
  record_danmaku: false,
  record_danmaku_super_chat: true,
  record_danmaku_gift: false,
  record_danmaku_guard: true,
  save_cover: false,
  retry_seconds: 6,
  check_interval_seconds: 180,
});
const reply = (settings = preferences(), revision = "a".repeat(64)) => ({
  available: true,
  message: "ok",
  adapter_version: "2.20.0",
  revision,
  settings,
  cookie_configured: true,
  quality_options: [{ value: "avc10000,hevc10000", label: "原画" }],
  notice: "notice",
});
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
};

test("settings load on opening only; suspension clears credentials but keeps ordinary draft", async () => {
  let reads = 0;
  const dirty = [];
  const service = new RecorderSettingsService(
    async () => {
      reads++;
      return reply();
    },
    (value) => dirty.push(value),
  );
  assert.equal(reads, 0);
  await service.open();
  assert.equal(reads, 0);
  service.setActive(true);
  await service.open();
  assert.equal(reads, 1);
  service.update("save_cover", true);
  service.setCookie("SESSDATA=private-value");
  service.setActive(false);
  assert.equal(service.getSnapshot().cookie, "");
  assert.equal(service.getSnapshot().open, false);
  assert.equal(service.getSnapshot().draft.save_cover, true);
  assert.equal(service.getSnapshot().dirty, true);
  service.setActive(true);
  await service.open();
  assert.equal(reads, 1);
  assert.ok(dirty.includes(true));
});

test("save sends one revision-bound patch, clears secret and acknowledges the saved snapshot", async () => {
  const calls = [];
  const service = new RecorderSettingsService(async (path, init = {}) => {
    calls.push({ path, init });
    if (init.method === "PUT") {
      const body = JSON.parse(init.body);
      return reply(body.settings, "b".repeat(64));
    }
    return reply();
  });
  service.setActive(true);
  await service.open();
  service.update("split_mode", "time");
  service.update("split_value", 30);
  service.setCookie("SESSDATA=private-new");
  await service.save();
  const write = calls.find((x) => x.init.method === "PUT");
  const body = JSON.parse(write.init.body);
  assert.equal(body.revision, "a".repeat(64));
  assert.equal(body.settings.split_value, 30);
  assert.equal(body.cookie, "SESSDATA=private-new");
  assert.equal(service.getSnapshot().cookie, "");
  assert.equal(service.getSnapshot().dirty, false);
  assert.equal(service.getSnapshot().reply.revision, "b".repeat(64));
  assert.equal(calls.filter((x) => x.init.method === "PUT").length, 1);
});

test("late load cannot replace a newer reopened settings session", async () => {
  const old = deferred(),
    fresh = deferred();
  let calls = 0;
  const service = new RecorderSettingsService(async () =>
    ++calls === 1 ? old.promise : fresh.promise,
  );
  service.setActive(true);
  const opening = service.open();
  service.close();
  const reopening = service.open();
  fresh.resolve(reply({ ...preferences(), save_cover: true }, "b".repeat(64)));
  await reopening;
  old.resolve(reply());
  await opening;
  assert.equal(service.getSnapshot().draft.save_cover, true);
  assert.equal(service.getSnapshot().reply.revision, "b".repeat(64));
});

test("conflict keeps non-secret edits and requires an explicit reload", async () => {
  let writes = 0;
  const service = new RecorderSettingsService(async (path, init = {}) => {
    if (init.method === "PUT") {
      writes++;
      throw Object.assign(new Error("设置已更新"), { status: 409 });
    }
    return reply();
  });
  service.setActive(true);
  await service.open();
  service.update("record_danmaku", true);
  service.setCookie("SESSDATA=private");
  await service.save();
  assert.equal(service.getSnapshot().conflict, true);
  assert.equal(service.getSnapshot().draft.record_danmaku, true);
  assert.equal(service.getSnapshot().cookie, "");
  assert.equal(service.getSnapshot().dirty, true);
  await service.save();
  assert.equal(writes, 1);
  await service.reload();
  assert.equal(service.getSnapshot().draft.record_danmaku, false);
  assert.equal(service.getSnapshot().dirty, false);
});

test("a pending write is not repeated and cannot repopulate secrets after closing", async () => {
  const pending = deferred();
  let writes = 0;
  const service = new RecorderSettingsService(async (path, init = {}) => {
    if (init.method === "PUT") {
      writes++;
      return pending.promise;
    }
    return reply();
  });
  service.setActive(true);
  await service.open();
  service.setCookie("SESSDATA=private");
  const saving = service.save();
  await service.save();
  service.close();
  await service.open();
  assert.equal(writes, 1);
  assert.equal(service.getSnapshot().cookie, "");
  pending.resolve(reply());
  await saving;
  assert.equal(service.getSnapshot().cookie, "");
  assert.match(service.getSnapshot().notice, /重新读取/);
  assert.equal(service.getSnapshot().saving, false);
});

test("clear Cookie is explicit and incompatible settings are never turned into editable defaults", async () => {
  let payload;
  const service = new RecorderSettingsService(async (path, init = {}) => {
    if (init.method === "PUT") {
      payload = JSON.parse(init.body);
      return {
        ...reply(payload.settings, "b".repeat(64)),
        cookie_configured: false,
      };
    }
    return reply();
  });
  service.setActive(true);
  await service.open();
  service.setClearCookie(true);
  await service.save();
  assert.equal(payload.clear_cookie, true);
  assert.ok(!("cookie" in payload));
  assert.equal(service.getSnapshot().reply.cookie_configured, false);
  const unavailable = new RecorderSettingsService(async () => ({
    available: false,
    message: "版本不兼容",
    adapter_version: "2.20.0",
  }));
  unavailable.setActive(true);
  await unavailable.open();
  assert.equal(unavailable.getSnapshot().draft, null);
  await unavailable.save();
  const malformed = new RecorderSettingsService(async () => ({
    available: true,
    message: "wrong",
  }));
  malformed.setActive(true);
  await malformed.open();
  assert.equal(malformed.getSnapshot().draft, null);
  assert.match(malformed.getSnapshot().error, /响应异常/);
});

test("client range checks stop invalid writes without changing backend authority", async () => {
  let writes = 0;
  const service = new RecorderSettingsService(async (path, init = {}) => {
    if (init.method === "PUT") writes++;
    return reply();
  });
  service.setActive(true);
  await service.open();
  service.update("retry_seconds", 0);
  await service.save();
  assert.equal(writes, 0);
  assert.match(service.getSnapshot().error, /1–120/);
  service.update("split_mode", "time");
  service.update("split_value", NaN);
  await service.save();
  assert.equal(writes, 0);
  assert.match(service.getSnapshot().error, /整数/);
});
