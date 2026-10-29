import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const { outputText } = ts.transpileModule(
  await readFile(new URL("../src/api.ts", import.meta.url), "utf8"),
  {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  },
);
let moduleId = 0;
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status });
const expired = () => json({ detail: "会话已失效，请刷新工作台" }, 403);
const tick = () => new Promise(setImmediate);
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => {
    resolve = r;
  });
  return { promise, resolve };
};
const project = {
  id: "draft",
  revision: 8,
  clips: [{ id: "unsaved", title: "未保存的标注" }],
  detector_device: "cpu",
};

async function fixture(t, fetch) {
  const originalFetch = globalThis.fetch;
  const originalXHR = globalThis.XMLHttpRequest;
  globalThis.fetch = fetch;
  const requests = [];
  globalThis.XMLHttpRequest = class {
    upload = {};
    headers = {};
    constructor() {
      requests.push(this);
    }
    open(method, url) {
      this.method = method;
      this.url = url;
    }
    setRequestHeader(name, value) {
      this.headers[name] = value;
    }
    send(body) {
      this.body = body;
    }
    abort() {
      this.aborted = true;
      this.onabort?.();
    }
    respond(status, body) {
      this.status = status;
      this.responseText =
        typeof body === "string" ? body : JSON.stringify(body);
      this.onload();
    }
  };
  t.after(() => {
    globalThis.fetch = originalFetch;
    globalThis.XMLHttpRequest = originalXHR;
  });
  const module = await import(
    `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}#${moduleId++}`
  );
  return { ...module, requests };
}

test("a server restart refreshes the token and replays the unchanged save/POST only after middleware rejection", async (t) => {
  let liveToken = "before-restart";
  let sessions = 0;
  const attempts = [];
  const mutations = [];
  const { api } = await fixture(t, async (url, init) => {
    if (url === "/api/session") {
      sessions++;
      assert.equal(init.cache, "no-store");
      return json({ token: liveToken });
    }
    attempts.push({ url, init });
    if (init.headers.get("X-Studio-Token") !== liveToken) return expired();
    mutations.push({ url, body: init.body });
    return json({ ...project, revision: project.revision + 1 });
  });
  await api.save(project);
  liveToken = "after-restart";
  assert.equal((await api.save(project)).revision, 9);
  assert.equal(sessions, 2);
  assert.equal(mutations.length, 2);
  assert.equal(attempts.length, 3);
  assert.equal(attempts[1].init.body, attempts[2].init.body);
  assert.equal(
    attempts[2].init.headers.get("Content-Type"),
    "application/json",
  );
  assert.deepEqual(JSON.parse(mutations[1].body).clips, project.clips);
  liveToken = "third-process";
  await api.lyrics("draft", "unsaved", "歌词", 50);
  assert.equal(sessions, 3);
  assert.equal(mutations.length, 3);
  assert.equal(attempts.at(-1).init.method, "POST");
  assert.equal(attempts.at(-2).init.body, attempts.at(-1).init.body);
});

test("unrelated 403, 401, malformed JSON, and ambiguous failures never replay a mutation", async (t) => {
  const cases = [
    {
      response: () => json({ detail: "请求来源不在工作台允许列表中" }, 403),
      match: /请求来源/,
    },
    {
      response: () => json({ message: "会话已失效，请刷新工作台" }, 403),
      match: /会话已失效/,
    },
    {
      response: () => new Response("<html>Forbidden</html>", { status: 403 }),
      match: /403/,
    },
    {
      response: () => new Response("<html>Login</html>", { status: 401 }),
      match: /服务器账户重新登录/,
    },
    {
      response: () => json({ detail: "会话已失效，请刷新工作台" }, 500),
      match: /会话已失效/,
    },
    {
      response: () => new Response("not json"),
      match: /响应异常.*确认操作是否已完成/,
    },
    {
      response: () => {
        throw new TypeError("Network disconnected after submitting");
      },
      match: /Network disconnected/,
    },
  ];
  for (const [index, scenario] of cases.entries()) {
    await t.test(`boundary ${index + 1}`, async (t) => {
      let sessions = 0;
      let attempts = 0;
      const { api } = await fixture(t, async (url) => {
        if (url === "/api/session") {
          sessions++;
          return json({ token: "valid-token" });
        }
        attempts++;
        return scenario.response();
      });
      await assert.rejects(api.save(project), scenario.match);
      assert.equal(sessions, 1);
      assert.equal(attempts, 1);
    });
  }
});

test("a second explicit session rejection stops after one replay", async (t) => {
  let sessions = 0;
  let attempts = 0;
  const { api } = await fixture(t, async (url) => {
    if (url === "/api/session") return json({ token: `process-${++sessions}` });
    attempts++;
    return expired();
  });
  await assert.rejects(
    api.save(project),
    (error) => error.status === 403 && /会话已失效/.test(error.message),
  );
  assert.equal(sessions, 2);
  assert.equal(attempts, 2);
});

test("invalid/unauthorized session responses never send a mutation and failed lookups can recover", async (t) => {
  for (const response of [
    () => new Response("broken json"),
    () => json({ token: "" }),
    () => json({ token: 42 }),
    () => new Response("login required", { status: 401 }),
  ]) {
    await t.test("session validation", async (t) => {
      let sessions = 0;
      let mutations = 0;
      const { api } = await fixture(t, async (url) => {
        if (url === "/api/session")
          return ++sessions === 1 ? response() : json({ token: "recovered" });
        mutations++;
        return json(project);
      });
      await assert.rejects(
        api.save(project),
        /会话响应异常|服务器账户重新登录/,
      );
      assert.equal(mutations, 0);
      await api.save(project);
      assert.equal(mutations, 1);
      assert.equal(sessions, 2);
    });
  }
});

test("concurrent and late stale responses share the same fresh session", async (t) => {
  const lateRejection = deferred();
  const refresh = deferred();
  let sessions = 0;
  let staleAttempts = 0;
  let mutations = 0;
  const { api } = await fixture(t, async (url, init) => {
    if (url === "/api/session")
      return ++sessions === 1 ? json({ token: "old" }) : refresh.promise;
    if (init.headers.get("X-Studio-Token") === "old") {
      staleAttempts++;
      return staleAttempts === 1 ? expired() : lateRejection.promise;
    }
    mutations++;
    return json(project);
  });
  const first = api.save(project);
  const second = api.save({ ...project, id: "second-draft" });
  await tick();
  assert.equal(staleAttempts, 2);
  assert.equal(sessions, 2);
  refresh.resolve(json({ token: "fresh" }));
  await first;
  lateRejection.resolve(expired());
  await second;
  assert.equal(sessions, 2);
  assert.equal(mutations, 2);
});

test("aborting a save while refreshing stops that replay without cancelling shared refresh", async (t) => {
  const refresh = deferred();
  let sessions = 0;
  let mutations = 0;
  const { api } = await fixture(t, async (url, init) => {
    if (url === "/api/session")
      return ++sessions === 1 ? json({ token: "old" }) : refresh.promise;
    if (init.headers.get("X-Studio-Token") === "old") return expired();
    mutations++;
    return json(project);
  });
  const controller = new AbortController();
  const cancelled = api.save(project, controller.signal);
  const rejected = assert.rejects(cancelled, { name: "AbortError" });
  const kept = api.save({ ...project, id: "kept" });
  await tick();
  controller.abort();
  await rejected;
  refresh.resolve(json({ token: "new" }));
  await kept;
  assert.equal(mutations, 1);
  assert.equal(sessions, 2);
  const alreadyAborted = new AbortController();
  alreadyAborted.abort();
  await assert.rejects(api.save(project, alreadyAborted.signal), {
    name: "AbortError",
  });
  assert.equal(mutations, 1);
});

test("XHR upload and fetch share refresh; upload resets progress, preserves multipart, and mutates once", async (t) => {
  const refresh = deferred();
  let sessions = 0;
  let saves = 0;
  const { api, requests } = await fixture(t, async (url, init) => {
    if (url === "/api/session")
      return ++sessions === 1 ? json({ token: "old" }) : refresh.promise;
    if (init.headers.get("X-Studio-Token") === "old") return expired();
    saves++;
    return json(project);
  });
  const progress = [];
  const upload = api.upload(new File(["media"], "recording.mkv"), (p) =>
    progress.push(p),
  );
  await tick();
  const first = requests[0];
  first.upload.onprogress({ loaded: 5, total: 5, lengthComputable: true });
  first.upload.onload();
  first.respond(403, { detail: "会话已失效，请刷新工作台" });
  const save = api.save(project);
  await tick();
  assert.equal(sessions, 2);
  assert.deepEqual(progress.at(-1), {
    phase: "uploading",
    loaded: 0,
    total: null,
  });
  assert.equal(requests.length, 1);
  refresh.resolve(json({ token: "new" }));
  await tick();
  const retry = requests[1];
  assert.equal(retry.body, first.body);
  assert.equal(retry.headers["X-Studio-Token"], "new");
  assert.equal(retry.body.get("file").name, "recording.mkv");
  retry.respond(201, { id: "imported-once" });
  assert.equal((await upload).id, "imported-once");
  await save;
  assert.equal(saves, 1);
  assert.equal(requests.length, 2);
  assert.equal(sessions, 2);
});

test("XHR stops after the second expiry and all ambiguous failures remain single submissions", async (t) => {
  const scenarios = [
    {
      status: 403,
      body: { detail: "请求来源不在工作台允许列表中" },
      match: /请求来源/,
    },
    {
      status: 403,
      body: { message: "会话已失效，请刷新工作台" },
      match: /会话已失效/,
    },
    { status: 401, body: "login required", match: /服务器账户重新登录/ },
    { status: 201, body: "invalid json", match: /确认素材是否已导入/ },
    { event: "onerror", match: /上传连接中断/ },
    { event: "ontimeout", match: /上传超时/ },
    {
      status: 403,
      body: { detail: "会话已失效，请刷新工作台" },
      replay: true,
      match: /会话已失效/,
    },
  ];
  for (const scenario of scenarios) {
    await t.test(scenario.event ?? String(scenario.status), async (t) => {
      let sessions = 0;
      const { api, requests } = await fixture(t, async () =>
        json({ token: `session-${++sessions}` }),
      );
      const upload = api.upload(new File(["media"], "test.flv"));
      const rejected = assert.rejects(upload, scenario.match);
      await tick();
      if (scenario.event) requests[0][scenario.event]();
      else requests[0].respond(scenario.status, scenario.body);
      if (scenario.replay) {
        await tick();
        requests[1].respond(scenario.status, scenario.body);
      }
      await rejected;
      assert.equal(sessions, scenario.replay ? 2 : 1);
      assert.equal(requests.length, scenario.replay ? 2 : 1);
    });
  }
});

test("cancelled uploads settle while waiting for refresh and never resend; active upload aborts XHR", async (t) => {
  const refresh = deferred();
  let sessions = 0;
  const { api, requests } = await fixture(t, async () =>
    ++sessions === 1 ? json({ token: "old" }) : refresh.promise,
  );
  const controller = new AbortController();
  const upload = api.upload(
    new File(["media"], "test.flv"),
    undefined,
    controller.signal,
  );
  const rejected = assert.rejects(upload, { name: "AbortError" });
  await tick();
  requests[0].respond(403, { detail: "会话已失效，请刷新工作台" });
  await tick();
  controller.abort();
  await rejected;
  refresh.resolve(json({ token: "new" }));
  await tick();
  assert.equal(requests.length, 1);
  const active = new AbortController();
  const activeUpload = api.upload(
    new File(["media"], "test.flv"),
    undefined,
    active.signal,
  );
  const activeRejected = assert.rejects(activeUpload, { name: "AbortError" });
  await tick();
  active.abort();
  await activeRejected;
  assert.equal(requests[1].aborted, true);
  assert.equal(requests.length, 2);
});

test("an upload rejects cleanly when the shared session refresh fails instead of hanging or resending", async (t) => {
  let sessions = 0;
  const { api, requests } = await fixture(t, async () => {
    sessions++;
    return sessions === 1
      ? json({ token: "old" })
      : new Response("login required", { status: 401 });
  });
  const upload = api.upload(new File(["media"], "test.flv"));
  const rejected = assert.rejects(
    upload,
    (error) => error.status === 401 && /服务器账户重新登录/.test(error.message),
  );
  await tick();
  requests[0].respond(403, { detail: "会话已失效，请刷新工作台" });
  await rejected;
  assert.equal(sessions, 2);
  assert.equal(requests.length, 1);
});

test('account changes prevent a stale save from replaying under a new cookie',async t=>{
  const late=deferred();let mutations=0;
  const {api,resetApiSession}=await fixture(t,async url=>{
    if(url==='/api/session')return json({token:'old-token'});
    mutations++;return late.promise;
  });
  const pending=api.save(project);await tick();resetApiSession();late.resolve(expired());
  await assert.rejects(pending,{name:'AbortError'});assert.equal(mutations,1);
});

test('account changes prevent a stale upload from retrying under a new cookie',async t=>{
  const {api,requests,resetApiSession}=await fixture(t,async()=>json({token:'old-token'}));
  const pending=api.upload(new Blob(['test']));await tick();assert.equal(requests.length,1);
  resetApiSession();requests[0].respond(403,{detail:'会话已失效，请刷新工作台'});
  await assert.rejects(pending,{name:'AbortError'});assert.equal(requests.length,1);
});
