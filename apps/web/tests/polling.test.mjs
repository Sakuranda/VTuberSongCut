import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";
const { outputText } = ts.transpileModule(
  await readFile(new URL("../src/polling.ts", import.meta.url), "utf8"),
  {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
    },
  },
);
const { startVisiblePolling } = await import(
  `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`
);
const settle = () => new Promise(setImmediate);
class Visibility extends EventTarget {
  hidden = false;
  change(hidden) {
    this.hidden = hidden;
    this.dispatchEvent(new Event("visibilitychange"));
  }
}
function fixture(hidden = false) {
  const visibility = new Visibility();
  visibility.hidden = hidden;
  const timers = new Map();
  let next = 0;
  return {
    visibility,
    timers,
    options: {
      interval: 2000,
      visibility,
      schedule: (callback) => {
        timers.set(++next, callback);
        return next;
      },
      cancel: (id) => timers.delete(id),
    },
    fire() {
      const [id, fn] = timers.entries().next().value;
      timers.delete(id);
      fn();
    },
  };
}

test("slow responses never overlap; hiding cancels requests and resuming reads immediately", async () => {
  const f = fixture();
  const requests = [];
  let finish;
  const stop = startVisiblePolling((signal) => {
    requests.push(signal);
    return new Promise((resolve) => {
      finish = resolve;
    });
  }, f.options);
  assert.equal(requests.length, 1);
  assert.equal(f.timers.size, 0);
  f.visibility.change(true);
  assert.equal(requests[0].aborted, true);
  f.visibility.change(false);
  assert.equal(requests.length, 1);
  finish();
  await settle();
  assert.equal(requests.length, 2);
  finish();
  await settle();
  assert.equal(f.timers.size, 1);
  f.visibility.change(true);
  assert.equal(f.timers.size, 0);
  stop();
  f.visibility.change(false);
  assert.equal(requests.length, 2);
});

test("hidden pages make no requests, terminal tasks stop, and stop aborts outstanding work", async () => {
  const f = fixture(true);
  let calls = 0;
  const stop = startVisiblePolling(async () => {
    calls++;
    return false;
  }, f.options);
  assert.equal(calls, 0);
  f.visibility.change(false);
  await settle();
  assert.equal(calls, 1);
  assert.equal(f.timers.size, 0);
  stop();
  f.visibility.change(true);
  f.visibility.change(false);
  assert.equal(calls, 1);
  let signal;
  const g = fixture();
  const stopPending = startVisiblePolling((s) => {
    signal = s;
    return new Promise(() => {});
  }, g.options);
  stopPending();
  assert.equal(signal.aborted, true);
  assert.equal(g.timers.size, 0);
});

test("transient failures retry only after settling and leave no timer after cleanup", async () => {
  const f = fixture();
  let calls = 0;
  const stop = startVisiblePolling(async () => {
    calls++;
    throw new Error("offline");
  }, f.options);
  await settle();
  assert.equal(calls, 1);
  assert.equal(f.timers.size, 1);
  f.fire();
  await settle();
  assert.equal(calls, 2);
  assert.equal(f.timers.size, 1);
  stop();
  assert.equal(f.timers.size, 0);
});
