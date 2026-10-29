import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";
const { outputText } = ts.transpileModule(
  await readFile(
    new URL("../src/auth/ServerSession.ts", import.meta.url),
    "utf8",
  ),
  {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  },
);
const { ServerSession } = await import(
  "data:text/javascript;base64," + Buffer.from(outputText).toString("base64")
);
const json = (value, status = 200) =>
  new Response(JSON.stringify(value), { status });
const state = (name = "alice", role = "editor") => ({
  server: true,
  mode: "totp",
  authenticated: true,
  user: { name, role },
});
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => (resolve = r));
  return { promise, resolve };
};

test("workspace identity survives reauthentication but changes for another account or role", async () => {
  let user = state();
  const service = new ServerSession(async () => json(user));
  await service.check();
  const first = service.getSnapshot().workspaceKey;
  service.lock();
  assert.equal(service.getSnapshot().phase, "locked");
  assert.equal(service.getSnapshot().workspaceKey, first);
  await service.login({ username: "alice", password: "test", code: "000000" });
  assert.equal(service.getSnapshot().workspaceKey, first);
  service.lock();
  user = state("bob");
  await service.login({ username: "bob", password: "test", code: "000000" });
  assert.notEqual(service.getSnapshot().workspaceKey, first);
});
test("credentials are sent only in a JSON body and never retained in session state", async () => {
  const requests = [];
  const service = new ServerSession(async (url, init) => {
    requests.push([url, init]);
    return json(state());
  });
  await service.login({
    username: "alice",
    password: "sensitive-test-password",
    code: "012345",
  });
  assert.equal(requests[0][0], "/api/auth/login");
  assert.equal(JSON.parse(requests[0][1].body).code, "012345");
  assert.ok(
    !JSON.stringify(service.getSnapshot()).includes("sensitive-test-password"),
  );
  assert.ok(!JSON.stringify(service.getSnapshot()).includes("012345"));
});
test("duplicate login is blocked and failures do not mount a workspace", async () => {
  const pending = deferred();
  let count = 0;
  const service = new ServerSession(async () => {
    count++;
    return pending.promise;
  });
  const first = service.login({ username: "a", password: "b", code: "123456" });
  await service.login({ username: "a", password: "b", code: "123456" });
  assert.equal(count, 1);
  pending.resolve(json({ detail: "账号、密码或验证码不正确" }, 401));
  await first;
  assert.equal(service.getSnapshot().phase, "locked");
  assert.equal(service.getSnapshot().workspaceKey, undefined);
});
test("logout clears the mounted workspace even if background requests report expiry during logout", async () => {
  const response = deferred();
  const service = new ServerSession(async (url) =>
    url === "/api/auth/status"
      ? json(state())
      : url === "/api/session"
        ? json({ token: "csrf" })
        : response.promise,
  );
  await service.check();
  const leaving = service.logout();
  service.lock();
  response.resolve(json({ authenticated: false }));
  await leaving;
  assert.equal(service.getSnapshot().workspaceKey, undefined);
  assert.equal(service.getSnapshot().phase, "locked");
});
test("late status cannot unlock a newer failed status check; TLS errors are explicit", async () => {
  const old = deferred();
  let calls = 0;
  const service = new ServerSession(async () =>
    ++calls === 1
      ? old.promise
      : json({ detail: "请通过 HTTPS 地址登录" }, 403),
  );
  const first = service.check();
  await service.check();
  old.resolve(json(state()));
  await first;
  assert.equal(service.getSnapshot().phase, "error");
  assert.match(service.getSnapshot().error, /HTTPS/);
});
