import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import http from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { checkLoomSession, createUploadServer, readLoomCookie, uploadMp4 } from './loom-upload.mjs';

const COOKIE = 'loom_session=local-test-only';
const API_TOKEN = 'local-api-test-key';
const SITE = '00000000-0000-4000-8000-000000000001';
const ACTIVATION = '00000000-0000-4000-8000-000000000002';
const UPLOAD = '00000000-0000-4000-8000-000000000003';
const VIDEO = 'a1234567890123456789012345678901';
const URL = 'https://www.loom.com/share/' + VIDEO;
const STORAGE = 'https://object-store.atlassian.com';
const STORAGE_TOKEN = 'local-url-token+with=special-characters';
const BASE = '/loom-media-content/upload/loom/' + SITE + '/' + ACTIVATION;
const UUID = '[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}';
const expectedSteps = ['identity', 'credentials', 'open', 'part', 'close', 'publish'];
const json = (data, status = 200) => Response.json(data, { status });

async function fixture() {
  const path = fileURLToPath(new globalThis.URL('./fixtures/loom-kit-upload-test.mp4', import.meta.url));
  return [path, await readFile(path), 'CbgV4gmoZ9gVNQCp4MNlEMHti4LfWeNjLInOLvr9zg4='];
}

function loomMock(bytes, checksum, { foreignStorage = false, failPublish = false } = {}) {
  let sessionId;
  let objectId;
  const steps = [];
  async function fetchImpl(resource, options) {
    const target = new globalThis.URL(resource);
    const headers = new Headers(options.headers);
    assert.equal(options.redirect, 'error');
    if (target.origin === 'https://www.loom.com') {
      assert.equal(target.pathname, '/graphql');
      assert.equal(headers.get('cookie'), COOKIE);
      const { operationName, variables, query } = JSON.parse(options.body);
      assert.ok(query.includes(operationName));
      if (operationName === 'LoomUploadIdentity') {
        steps.push('identity');
        return json({ data: { me: { id: '42' } } });
      }
      if (operationName === 'CreateFileUploadCredentials') {
        steps.push('credentials');
        sessionId = variables.sessionId;
        assert.match(sessionId, new RegExp('^' + UUID + '$'));
        return json({ data: { createFileUploadCredentials: {
          __typename: 'CreateFileUploadCredentialsPayload',
          sessionId,
          credentials: {
            __typename: 'ObjectStoreInfo',
            hostname: foreignStorage ? 'https://foreign.invalid' : STORAGE,
            bucket: 'loom-media-content',
            workspaceType: 'loom',
            siteId: SITE,
            activationId: ACTIVATION,
            token: { value: STORAGE_TOKEN, expiration: Date.now() + 60_000 },
          },
        } } });
      }
      assert.equal(operationName, 'CompleteVideoFileUpload');
      steps.push('publish');
      assert.equal(variables.keyPath, objectId.slice('uploads-42-'.length));
      assert.equal(variables.fileName, 'Synthetic video');
      assert.equal(variables.videoProperties.file_size, bytes.length);
      assert.match(variables.videoProperties.file_anonymous_id, new RegExp('^' + UUID + '$'));
      assert.ok(!variables.keyPath.includes(variables.videoProperties.file_anonymous_id));
      assert.equal(variables.folderId, null);
      assert.equal(variables.spaceId, null);
      assert.equal(variables.privacy, 'public');
      if (failPublish) return json({ errors: [{ message: COOKIE, extensions: { code: 'INTERNAL_SERVER_ERROR' } }] });
      return json({ data: { video: { id: VIDEO, __typename: 'RegularUserVideo' } } });
    }
    assert.equal(target.origin, STORAGE);
    assert.equal(headers.get('cookie'), null);
    assert.equal(headers.get('authorization'), null);
    assert.equal(target.searchParams.get('authToken'), STORAGE_TOKEN);
    const routing = headers.get('x-loom-entity-routing');
    assert.equal(routing, 'eyJlbnRpdHlUeXBlIjoic2l0ZSIsImVudGl0eUlkIjoiMDAwMDAwMDAtMDAwMC00MDAwLTgwMDAtMDAwMDAwMDAwMDAxIn0');
    if (target.pathname === BASE + '/multipart') {
      steps.push('open');
      objectId = target.searchParams.get('objectId');
      assert.match(objectId, new RegExp('^uploads-42-' + sessionId + '-' + UUID + '\\.mp4$'));
      assert.equal(target.searchParams.get('contentType'), 'video/mp4');
      assert.equal(target.searchParams.get('shouldOverwrite'), 'false');
      // The real storage service rejects an open request without this header (HTTP 400, MissingChecksumType).
      assert.equal(new Headers(options.headers).get('x-content-checksum-type'), 'SHA256');
      assert.equal(options.method, 'POST');
      return json({ uploadId: UPLOAD, objectId, creationInfo: { by: 'loom/loom' }, uploadExpiresAt: 2_000_000_000 });
    }
    if (target.pathname === BASE + '/multipart/' + UPLOAD + '/parts') {
      steps.push('part');
      assert.equal(options.method, 'PUT');
      assert.deepEqual(options.body, bytes);
      assert.equal(headers.get('content-type'), 'application/octet-stream');
      assert.equal(headers.get('x-part-number'), '1');
      assert.equal(headers.get('x-content-checksum-type'), 'SHA256');
      assert.equal(headers.get('x-content-checksum'), checksum);
      return new Response(null, { status: 201 });
    }
    assert.equal(target.pathname, BASE + '/multipart/' + UPLOAD + '/complete');
    steps.push('close');
    assert.equal(options.method, 'POST');
    assert.equal(headers.get('x-expected-size'), String(bytes.length));
    return json({ uploadId: UPLOAD, objectId, size: bytes.length, contentType: 'video/mp4' }, 202);
  }
  return { fetchImpl, steps };
}

test('uploads bytes with the captured contract and returns the Loom share URL', async () => {
  const [path, bytes, checksum] = await fixture();
  const mock = loomMock(bytes, checksum);
  const result = await uploadMp4(path, { cookie: COOKIE, title: 'Synthetic video', privacy: 'public', fetchImpl: mock.fetchImpl });
  assert.deepEqual(result, { videoId: VIDEO, url: URL, privacy: 'public' });
  assert.deepEqual(mock.steps, expectedSteps);
});

test('rejects an unexpected storage host before sending credentials or video bytes', async () => {
  const [path, bytes, checksum] = await fixture();
  const mock = loomMock(bytes, checksum, { foreignStorage: true });
  await assert.rejects(uploadMp4(path, { cookie: COOKIE, title: 'Synthetic video', privacy: 'public', fetchImpl: mock.fetchImpl }),
    { code: 'unexpected_storage_origin' });
  assert.deepEqual(mock.steps, ['identity', 'credentials']);
});

test('publishing errors redact upstream text and preserve the existing upload reference', async () => {
  const [path, bytes, checksum] = await fixture();
  const mock = loomMock(bytes, checksum, { failPublish: true });
  await assert.rejects(uploadMp4(path, { cookie: COOKIE, title: 'Synthetic video', privacy: 'public', fetchImpl: mock.fetchImpl }), (error) => {
    assert.equal(error.stage, 'publish');
    assert.equal(error.uploadId, UPLOAD);
    assert.equal(error.outcomeUnknown, true);
    assert.ok(!error.message.includes(COOKIE));
    return true;
  });
  assert.deepEqual(mock.steps, expectedSteps);
});

test('the HTTP API accepts a chunked MP4 through its bearer-protected endpoint', async (t) => {
  const [, bytes, checksum] = await fixture();
  const mock = loomMock(bytes, checksum);
  const server = createUploadServer({ apiToken: API_TOKEN, getCookie: async () => COOKIE, fetchImpl: mock.fetchImpl });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const local = 'http://127.0.0.1:' + server.address().port;
  const denied = await fetch(local + '/upload', { method: 'POST', body: bytes });
  assert.equal(denied.status, 401);
  const missingVisibility = await fetch(local + '/upload', {
    method: 'POST',
    body: bytes,
    headers: { authorization: 'Bearer ' + API_TOKEN, 'content-type': 'video/mp4' },
  });
  assert.equal(missingVisibility.status, 400);
  assert.equal((await missingVisibility.json()).error, 'explicit_public_visibility_required');
  assert.deepEqual(mock.steps, []);
  const result = await new Promise((resolve, reject) => {
    const req = http.request(local + '/upload?title=Synthetic+video&privacy=public', {
      method: 'POST',
      headers: { authorization: 'Bearer ' + API_TOKEN, 'content-type': 'video/mp4' },
    });
    req.on('error', reject);
    req.on('response', async (res) => {
      try {
        let body = '';
        for await (const chunk of res) body += chunk;
        resolve({ status: res.statusCode, body: JSON.parse(body) });
      } catch (error) {
        reject(error);
      }
    });
    req.write(bytes.subarray(0, 1));
    req.end(bytes.subarray(1));
  });
  assert.deepEqual(result, { status: 201, body: { videoId: VIDEO, url: URL, privacy: 'public' } });
  assert.deepEqual(mock.steps, expectedSteps);
});

test('a publication with uncertain outcome produces an explicit HTTP conflict', async (t) => {
  const [, bytes, checksum] = await fixture();
  const mock = loomMock(bytes, checksum, { failPublish: true });
  const server = createUploadServer({ apiToken: API_TOKEN, getCookie: async () => COOKIE, fetchImpl: mock.fetchImpl });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const local = 'http://127.0.0.1:' + server.address().port;
  const response = await fetch(local + '/upload?title=Synthetic+video&privacy=public', {
    method: 'POST',
    body: bytes,
    headers: { authorization: 'Bearer ' + API_TOKEN, 'content-type': 'video/mp4' },
  });
  assert.equal(response.status, 409);
  const failure = await response.json();
  assert.equal(failure.outcomeUnknown, true);
  assert.equal(failure.uploadId, UPLOAD);
  assert.deepEqual(mock.steps, expectedSteps);
});

test('cancellation during account lookup stops the flow before requesting upload credentials', async () => {
  const [path, bytes, checksum] = await fixture();
  const mock = loomMock(bytes, checksum);
  const controller = new AbortController();
  async function fetchImpl(resource, options) {
    const response = await mock.fetchImpl(resource, options);
    controller.abort();
    return response;
  }
  await assert.rejects(uploadMp4(path, { cookie: COOKIE, title: 'Synthetic video', privacy: 'public',
    fetchImpl, signal: controller.signal }), { code: 'upload_cancelled' });
  assert.deepEqual(mock.steps, ['identity']);
});

test('one active API upload reserves the server before a second file can be spooled', async (t) => {
  const [, bytes, checksum] = await fixture();
  const mock = loomMock(bytes, checksum);
  const started = Promise.withResolvers();
  const resume = Promise.withResolvers();
  let paused = false;
  async function fetchImpl(resource, options) {
    if (!paused) {
      paused = true;
      started.resolve();
      await resume.promise;
    }
    return mock.fetchImpl(resource, options);
  }
  const server = createUploadServer({ apiToken: API_TOKEN, getCookie: async () => COOKIE, fetchImpl });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const local = 'http://127.0.0.1:' + server.address().port;
  const send = () => fetch(local + '/upload?title=Synthetic+video&privacy=public', {
    method: 'POST',
    body: bytes,
    headers: { authorization: 'Bearer ' + API_TOKEN, 'content-type': 'video/mp4' },
  });
  const first = send();
  await started.promise;
  const second = await send();
  resume.resolve();
  assert.equal(second.status, 429);
  assert.equal((await first).status, 201);
  assert.deepEqual(mock.steps, expectedSteps);
});

test('the file-backed session rejects a readable-by-others cookie file', async (t) => {
  const folder = await mkdtemp(join(tmpdir(), 'loom-cookie-tests-'));
  t.after(() => rm(folder, { recursive: true, force: true }));
  const file = join(folder, 'session.cookie');
  await writeFile(file, COOKIE + '\n', { mode: 0o644 });
  await assert.rejects(readLoomCookie(file), { code: 'unsafe_secret_permissions' });
});

test('a session check asks Loom who is signed in and uploads nothing', async () => {
  const seen = [];
  const reply = (me) => async (url, options) => {
    seen.push([String(url), JSON.parse(options.body).operationName, options.headers.cookie]);
    return json({ data: { me } });
  };
  await checkLoomSession(COOKIE, reply({ id: '42' }));
  assert.deepEqual(seen, [['https://www.loom.com/graphql', 'LoomUploadIdentity', COOKIE]]);
  await assert.rejects(checkLoomSession(COOKIE, reply(null)), { code: 'loom_session_unavailable' });
});

test('run by hand, the uploader only looks for a session inside this package', async () => {
  const { DEFAULT_COOKIE_FILE } = await import('./loom-upload.mjs');
  const { fileURLToPath } = await import('node:url');
  assert.equal(DEFAULT_COOKIE_FILE, fileURLToPath(new globalThis.URL('../data/loom/session.cookie', import.meta.url)));
});
