import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import ts from 'typescript';

const { outputText } = ts.transpileModule(await readFile(new URL('../src/api.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
});
const { api } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`);
const dataset = { id: 'training-test', revision: 7, samples: [] };
const tick = () => new Promise(setImmediate);

function setup() {
  const originalXHR = globalThis.XMLHttpRequest;
  const originalFetch = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async () => new Response(JSON.stringify({ token: 'test-session' }));
  globalThis.XMLHttpRequest = class {
    upload = {};
    headers = {};
    constructor() { requests.push(this); }
    open(method, url) { this.method = method; this.url = url; }
    setRequestHeader(name, value) { this.headers[name] = value; }
    send(body) { this.body = body; }
  };
  return { requests, restore() { globalThis.XMLHttpRequest = originalXHR; globalThis.fetch = originalFetch; } };
}

test('upload reports actual transfer, then waits for server import instead of completing at 100%', async () => {
  const fixture = setup();
  try {
    const progress = [];
    let completed = false;
    const request = api.uploadTrainingSample(dataset, new File(['video'], 'test.flv'), item => progress.push(item))
      .then(result => { completed = true; return result; });
    await tick();
    const xhr = fixture.requests[0];
    assert.equal(xhr.headers['X-Studio-Token'], 'test-session');
    assert.equal(xhr.body.get('revision'), '7');
    xhr.upload.onprogress({ loaded: 50, total: 100, lengthComputable: true });
    assert.deepEqual(progress.at(-1), { phase: 'uploading', loaded: 50, total: 100 });
    xhr.upload.onprogress({ loaded: 100, total: 100, lengthComputable: true });
    xhr.upload.onload();
    await tick();
    assert.equal(progress.at(-1).phase, 'processing');
    assert.equal(completed, false);
    xhr.status = 201;
    xhr.responseText = JSON.stringify({ ...dataset, revision: 8 });
    xhr.onload();
    assert.equal((await request).revision, 8);
  } finally { fixture.restore(); }
});

test('upload exposes server import errors and plain proxy size limits', async () => {
  const fixture = setup();
  try {
    for (const [status, body, message] of [
      [422, JSON.stringify({ detail: '无法读取媒体：文件已损坏' }), /文件已损坏/],
      [413, '<html>too large</html>', /服务器允许的上传大小/],
      [201, 'null', /重新载入项目/],
    ]) {
      const request = api.uploadTrainingSample(dataset, new File(['bad'], 'bad.flv'));
      const rejected = assert.rejects(request, message);
      await tick();
      const xhr = fixture.requests.at(-1);
      xhr.status = status;
      xhr.responseText = body;
      xhr.onload();
      await rejected;
    }
  } finally { fixture.restore(); }
});

test('unknown upload length stays indeterminate and a network failure is actionable', async () => {
  const fixture = setup();
  try {
    const progress = [];
    const request = api.uploadTrainingSample(dataset, new File(['video'], 'test.flv'), item => progress.push(item));
    const rejected = assert.rejects(request, /上传连接中断/);
    await tick();
    const xhr = fixture.requests[0];
    xhr.upload.onprogress({ loaded: 100, total: 0, lengthComputable: false });
    assert.equal(progress[0].total, null);
    xhr.onerror();
    await rejected;
  } finally { fixture.restore(); }
});
