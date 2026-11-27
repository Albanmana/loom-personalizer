import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import { cdpClient, cookieHeader, findBrowser, saveSession, waitForSession } from './loom-login.mjs';

const cookie = (name, value, domain, path = '/') => ({ name, value, domain, path });

test('only cookies the browser would send to Loom are kept', () => {
  assert.equal(cookieHeader([
    cookie('connect.sid', 's%3Aabc', '.loom.com'),
    cookie('host', '1', 'www.loom.com'),
    cookie('google', 'x', '.google.com'),
    cookie('lookalike', 'x', '.evilloom.com'),
    cookie('other-host', 'x', 'api.loom.com'),
    cookie('other-path', 'x', '.loom.com', '/admin'),
  ]), 'connect.sid=s%3Aabc; host=1');
});

test('waiting ends once Loom accepts the session, asking only when the cookies change', async () => {
  const jars = [[], [cookie('visitor', '1', '.loom.com')], [cookie('visitor', '1', '.loom.com')],
    [cookie('connect.sid', 'ok', '.loom.com')]];
  const asked = [];
  let turn = 0;
  const header = await waitForSession({
    getCookies: async () => jars[Math.min(turn++, jars.length - 1)],
    isOpen: async () => true,
    check: async (value) => {
      asked.push(value);
      if (!value.includes('connect.sid')) throw new Error('signed out');
    },
    pause: async () => {},
  });
  assert.equal(header, 'connect.sid=ok');
  assert.deepEqual(asked, ['visitor=1', 'connect.sid=ok']);
});

test('closing the window or waiting too long stops with a clear reason', async () => {
  const signedOut = { getCookies: async () => [], check: async () => {}, pause: async () => {} };
  const once = [true];
  await assert.rejects(waitForSession({ ...signedOut, isOpen: async () => once.pop() ?? false }), /closed before/);
  let time = 0;
  await assert.rejects(waitForSession({ ...signedOut, isOpen: async () => true, now: () => (time += 60_000) }),
    /took too long/);
});

test('a window that has not appeared yet is not taken for a closed one', async () => {
  const open = [false, false, true, true];
  const header = await waitForSession({
    getCookies: async () => (open.length ? [] : [cookie('connect.sid', 'ok', '.loom.com')]),
    isOpen: async () => (open.length ? open.shift() : true),
    check: async () => {},
    pause: async () => {},
  });
  assert.equal(header, 'connect.sid=ok');
});

test('the saved session is private and replaces the old one in one step', async (t) => {
  const folder = await mkdtemp(join(tmpdir(), 'loom-login-tests-'));
  t.after(() => rm(folder, { recursive: true, force: true }));
  const file = join(folder, 'session.cookie');
  await writeFile(file, 'old=1\n', { mode: 0o644 });
  await saveSession(file, 'new=2');
  assert.equal(await readFile(file, 'utf8'), 'new=2\n');
  assert.equal((await stat(file)).mode & 0o777, 0o600);
  assert.deepEqual(await readdir(folder), ['session.cookie']);
});

test('browser messages are matched to their request even when split or mixed with events', async () => {
  const toBrowser = new PassThrough();
  const fromBrowser = new PassThrough();
  const client = cdpClient(toBrowser, fromBrowser);
  const sent = [];
  toBrowser.on('data', (data) => sent.push(...String(data).split('\0').filter(Boolean).map((m) => JSON.parse(m))));
  const first = client.send('Storage.getCookies');
  const second = client.send('Target.getTargets');
  fromBrowser.write('{"method":"Target.created"}\0{"id":2,"result":{"b":2}}\0{"id":1,"res');
  fromBrowser.write('ult":{"a":1}}\0');
  assert.deepEqual(await Promise.all([first, second]), [{ a: 1 }, { b: 2 }]);
  assert.deepEqual(sent.map((m) => m.method), ['Storage.getCookies', 'Target.getTargets']);
  const pending = client.send('Browser.close');
  fromBrowser.end();
  await assert.rejects(pending, /browser closed/i);
  await assert.rejects(client.send('Storage.getCookies'), /browser closed/i);
});

test('a Chromium browser is found, and none gives an instruction', () => {
  const brave = '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser';
  assert.equal(findBrowser({}, (path) => path === brave), brave);
  assert.equal(findBrowser({ LP_BROWSER: '/custom/chrome' }, () => true), '/custom/chrome');
  assert.throws(() => findBrowser({}, () => false), /Install Google Chrome/);
});
