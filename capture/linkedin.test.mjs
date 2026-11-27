import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { access, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { createServer } from 'node:http';
import { join } from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';
import { findBrowser } from '../uploader/loom-login.mjs';
import { allowedPage, blockReason, pageTarget, profileUrl, record, scrollOffsets } from './linkedin.mjs';

test('only a LinkedIn profile address over HTTPS is accepted, without its tracking part', () => {
  assert.equal(profileUrl(' https://linkedin.com/in/jane-doe?utm=1 '), 'https://www.linkedin.com/in/jane-doe/');
  for (const bad of ['http://www.linkedin.com/in/jane', 'https://evil.com/in/jane', 'https://www.linkedin.com/company/x',
    'https://user@www.linkedin.com/in/jane', 'https://www.linkedin.com.evil.com/in/jane', 'file:///etc/passwd',
    'https://www.linkedin.com/in/a/b', '']) {
    assert.throws(() => profileUrl(bad), /LinkedIn \/in\/ profile/, bad);
  }
});

test('any public web address can be recorded; only LinkedIn profiles use the LinkedIn sign-in', () => {
  assert.deepEqual(pageTarget('https://linkedin.com/in/jane-doe?utm=1'),
    { url: 'https://www.linkedin.com/in/jane-doe/', linkedin: true });
  assert.deepEqual(pageTarget(' https://acme.example.com/about?lang=en '),
    { url: 'https://acme.example.com/about?lang=en', linkedin: false });
  assert.deepEqual(pageTarget('http://acme.example.com'), { url: 'http://acme.example.com/', linkedin: false });
});

test('other LinkedIn pages, local files and addresses on this Mac or its network are refused', () => {
  for (const bad of ['https://www.linkedin.com/messaging/', 'https://fr.linkedin.com/feed/',
    'https://www.linkedin.com./messaging/', 'http://www.linkedin.com/in/jane']) {
    assert.throws(() => pageTarget(bad), /LinkedIn \/in\/ profile/, bad);
  }
  for (const bad of ['file:///etc/passwd', 'chrome://settings', 'https://user:pw@acme.example.com/', 'not a url', '',
    'http://localhost:5690/', 'http://app.localhost/', 'http://127.0.0.1:8896/', 'http://2130706433/',
    'http://[::1]/', 'http://192.168.1.1/', 'http://10.0.0.2/', 'http://172.20.0.1/', 'http://169.254.1.1/',
    'http://0.0.0.0/', 'http://printer.local/', 'http://router/']) {
    assert.throws(() => pageTarget(bad), /public web address/, bad);
  }
});

test('a domain name that merely starts like a private address is public', () => {
  for (const good of ['https://10.example.com/', 'https://127.example.org/', 'https://192.168.example.com/',
    'https://8.8.8.8/']) {
    assert.deepEqual(pageTarget(good), { url: good, linkedin: false }, good);
  }
});

test('a name that points to this Mac or its network is not a page that can be recorded', async () => {
  const pointsTo = (...addresses) => async () => addresses.map((address) => ({ address }));
  assert.equal(await allowedPage('https://acme.example.com/', false, pointsTo('93.184.216.34', '2606:2800::1')), true);
  for (const address of ['127.0.0.1', '10.0.0.2', '172.31.0.1', '192.168.1.1', '100.100.0.1', '169.254.1.1', '0.0.0.0',
    '::1', '::', 'fd00::1', 'fe80::1', '::ffff:192.168.1.1']) {
    assert.equal(await allowedPage('https://localtest.me/', false, pointsTo('93.184.216.34', address)), false, address);
  }
});

test('the page reached must be of the same kind as the one asked for', async () => {
  const never = async () => { throw new Error('LinkedIn needs no lookup'); };
  const outside = async () => [{ address: '93.184.216.34' }];
  assert.equal(await allowedPage('https://www.linkedin.com/in/jane/?trk=x', true, never), true);
  assert.equal(await allowedPage('https://www.linkedin.com/feed/', true, never), false);
  assert.equal(await allowedPage('https://acme.example.com/', true, outside), false);
  assert.equal(await allowedPage('https://www.linkedin.com/in/jane/', false, outside), false);
  assert.equal(await allowedPage('http://localhost:5690/', false, outside), false);
});

test('the scroll starts and ends at the top and lasts 13.4 seconds', () => {
  for (const travel of [20, 2160]) {
    const offsets = scrollOffsets(travel);
    assert.equal(offsets.length, 402);
    assert.deepEqual(new Set(offsets.slice(0, 27)), new Set([0]));
    assert.deepEqual(new Set(offsets.slice(-12)), new Set([0]));
    assert.equal(Math.max(...offsets), travel);
    assert.equal(Math.min(...offsets), 0);
  }
});

test('each swipe starts fast, glides to a stop and is followed by a real pause, like a trackpad', () => {
  const offsets = scrollOffsets(2160);
  const steps = offsets.map((value, frame) => value - (offsets[frame - 1] ?? 0));
  const swipes = [];
  let still = 99;
  for (const step of steps) {
    if (step === 0) {
      still += 1;
    } else {
      if (still >= 6) swipes.push([]);
      swipes.at(-1).push(Math.abs(step));
      still = 0;
    }
  }
  assert.equal(swipes.length, 6, 'four swipes down, one short one back up, then the return to the top');
  for (const swipe of swipes) {
    const fastest = swipe.indexOf(Math.max(...swipe));
    assert.ok(fastest < swipe.length / 3, `fastest at ${fastest} of ${swipe.length}`);
  }
  assert.equal(offsets[180], offsets[202], 'reading pause before the small swipe back');
  assert.ok(offsets[235] < offsets[202], 'one swipe goes back up a little');
});

test('sign-in walls, security checks and refusals stop the capture with what to do', () => {
  const page = { url: 'https://www.linkedin.com/in/jane/', status: 200, challenge: false, signIn: false };
  assert.equal(blockReason(page), null);
  assert.match(blockReason({ ...page, url: 'https://www.linkedin.com/authwall?x=1' }), /studio\.py linkedin/);
  assert.match(blockReason({ ...page, signIn: true }), /studio\.py linkedin/);
  assert.match(blockReason({ ...page, url: 'https://www.linkedin.com/checkpoint/lg' }), /security check/);
  assert.match(blockReason({ ...page, challenge: true }), /security check/);
  assert.match(blockReason({ ...page, status: 999 }), /denied/);
  assert.match(blockReason({ ...page, status: 404 }), /did not load/);
});

let browser = true;
try { findBrowser(); } catch { browser = false; }

const html = (body) => `<!doctype html><meta charset="utf-8"><style>body{margin:0;background:#fff}</style>${body}`;

async function folderFor(t) {
  const folder = await mkdtemp(join(tmpdir(), 'linkedin-tests-'));
  t.after(() => rm(folder, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 }));
  return folder;
}

test('a real browser records the page, without the private message list', { skip: !browser, timeout: 90_000 },
  async (t) => {
    const folder = await folderFor(t);
    const page = join(folder, 'profile.html');
    await writeFile(page, html(`<main><h1>Jane Doe</h1><div style="height:3000px"></div></main>
      <div id="msg-overlay" style="position:fixed;right:0;bottom:0;width:400px;height:300px;background:#f00"></div>`));
    const out = join(folder, 'out.mp4');
    await record(pathToFileURL(page).href, out, { profile: join(folder, 'browser'), isProfile: () => true });
    const info = String(execFileSync('ffprobe', ['-v', 'error', '-count_frames', '-show_entries',
      'stream=width,height,nb_read_frames', '-of', 'csv=p=0', out])).trim().replace(/,$/, '');
    assert.equal(info, '1600,900,402');
    const pixel = execFileSync('ffmpeg', ['-v', 'error', '-i', out, '-frames:v', '1', '-vf',
      'crop=2:2:1500:800,scale=1:1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-']);
    assert.ok(pixel[0] > 230 && pixel[1] > 230 && pixel[2] > 230, `message list visible: ${[...pixel]}`);
  });

test('a sign-in page is never recorded', { skip: !browser, timeout: 90_000 }, async (t) => {
  const folder = await folderFor(t);
  const page = join(folder, 'wall.html');
  await writeFile(page, html('<main><h1>Join</h1><input name="session_key"><div style="height:3000px"></div></main>'));
  const out = join(folder, 'out.mp4');
  await assert.rejects(record(pathToFileURL(page).href, out,
    { profile: join(folder, 'browser'), isProfile: () => true }), /studio\.py linkedin/);
  await assert.rejects(access(out));
});

// LinkedIn's current layout: the name is an h2, an inner area scrolls, and messaging lives in a shadow root.
const current = (host) => html(`<style>html,body{height:100%;overflow:hidden}</style>
  <main id="workspace" style="height:848px;margin-top:52px;overflow-y:auto">
    <h2>Jane Doe</h2><div style="height:1800px"></div><div style="height:1800px;background:#00f"></div></main>
  <div id="${host}"></div>
  <script>document.getElementById('${host}').attachShadow({ mode: 'open' }).innerHTML =
    '<div class="msg-overlay-list-bubble" style="position:fixed;right:0;bottom:0;width:400px;height:300px;background:#f00"></div>';</script>`);

const pixelAt = (movie, frame, x, y) => [...execFileSync('ffmpeg', ['-v', 'error', '-i', movie, '-vf',
  `select=eq(n\\,${frame}),crop=2:2:${x}:${y},scale=1:1`, '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])];

test('the current layout is scrolled to the bottom of its range and back, with messaging hidden',
  { skip: !browser, timeout: 90_000 }, async (t) => {
    const folder = await folderFor(t);
    const page = join(folder, 'current.html');
    await writeFile(page, current('interop-outlet'));
    const out = join(folder, 'out.mp4');
    await record(pathToFileURL(page).href, out, { profile: join(folder, 'browser'), isProfile: () => true, wait: 8000 });
    const [red, green, blue] = pixelAt(out, 310, 700, 700); // The furthest point of the scroll.
    assert.ok(blue > 200 && red < 60 && green < 60, `did not scroll: ${[red, green, blue]}`);
    for (const frame of [0, 401]) {
      const corner = pixelAt(out, frame, 1500, 800);
      assert.ok(corner.every((v) => v > 230), `frame ${frame} shows messaging: ${corner}`);
    }
  });

test('nothing is recorded when messaging cannot be hidden', { skip: !browser, timeout: 90_000 }, async (t) => {
  const folder = await folderFor(t);
  const page = join(folder, 'renamed.html');
  await writeFile(page, current('some-new-outlet'));
  const out = join(folder, 'out.mp4');
  await assert.rejects(record(pathToFileURL(page).href, out,
    { profile: join(folder, 'browser'), isProfile: () => true, wait: 8000 }), /message list could not be hidden/);
  await assert.rejects(access(out));
});

// LinkedIn's content security policy ignores added <style> tags.
test('messaging is hidden even when the site refuses added style tags', { skip: !browser, timeout: 90_000 },
  async (t) => {
    const folder = await folderFor(t);
    const page = join(folder, 'strict.html');
    await writeFile(page, `<!doctype html><meta charset="utf-8">
      <meta http-equiv="Content-Security-Policy" content="style-src 'nonce-t'; script-src 'nonce-t'">
      <style nonce="t">body{margin:0;background:#fff}.tall{height:3000px}</style>
      <main><h1>Jane Doe</h1><div class="tall"></div></main><div id="interop-outlet"></div>
      <script nonce="t">const box = document.createElement('div'); box.className = 'msg-overlay-list-bubble';
        box.style.cssText = 'position:fixed;right:0;bottom:0;width:400px;height:300px;background:#f00';
        document.getElementById('interop-outlet').attachShadow({ mode: 'open' }).append(box);</script>`);
    const out = join(folder, 'out.mp4');
    await record(pathToFileURL(page).href, out, { profile: join(folder, 'browser'), isProfile: () => true, wait: 8000 });
    const corner = pixelAt(out, 0, 1500, 800);
    assert.ok(corner.every((v) => v > 230), `message list visible: ${corner}`);
  });

// LinkedIn shows grey placeholders and fills some sections only once they are scrolled to.
test('sections are loaded before the first recorded frame', { skip: !browser, timeout: 90_000 }, async (t) => {
  const folder = await folderFor(t);
  const page = join(folder, 'lazy.html');
  await writeFile(page, html(`<main><h1>Jane Doe</h1><div style="height:3000px"></div></main>
    <div id="placeholder" style="position:absolute;left:200px;top:300px;width:600px;height:300px;background:#888"></div>
    <script>addEventListener('scroll', () => { if (scrollY > 600) setTimeout(() =>
      document.getElementById('placeholder').style.background = '#0f0', 600); });</script>`));
  const out = join(folder, 'out.mp4');
  await record(pathToFileURL(page).href, out, { profile: join(folder, 'browser'), isProfile: () => true, wait: 8000 });
  const [red, green, blue] = pixelAt(out, 0, 500, 450);
  assert.ok(green > 200 && red < 60 && blue < 60, `still loading in the first frame: ${[red, green, blue]}`);
});

test('a profile that LinkedIn says is unavailable is never recorded', { skip: !browser, timeout: 90_000 }, async (t) => {
  for (const heading of ['This profile is not available', 'Page not found']) {
    const folder = await folderFor(t);
    const page = join(folder, 'gone.html');
    await writeFile(page, html(`<main><h1>${heading}</h1><div style="height:3000px"></div></main>`));
    const out = join(folder, 'out.mp4');
    await assert.rejects(record(pathToFileURL(page).href, out,
      { profile: join(folder, 'browser'), isProfile: () => true, wait: 8000 }), /available profile/);
    await assert.rejects(access(out));
  }
});

test('another website needs no profile heading, and its own sign-in box does not stop the capture',
  { skip: !browser, timeout: 90_000 }, async (t) => {
    const folder = await folderFor(t);
    const page = join(folder, 'site.html');
    await writeFile(page, html('<main><p>Welcome</p><input name="session_password"><div style="height:3000px"></div></main>'));
    const out = join(folder, 'out.mp4');
    await record(pathToFileURL(page).href, out,
      { profile: join(folder, 'browser'), isProfile: () => true, linkedin: false, wait: 8000 });
    await access(out);
  });

test('another website that rewrites its own address just after loading is still recorded',
  { skip: !browser, timeout: 90_000 }, async (t) => {
    const folder = await folderFor(t);
    const server = createServer((request, response) => response.writeHead(200, { 'content-type': 'text/html' })
      .end(html('<main><div style="height:3000px"></div></main>'
        + '<script>setTimeout(() => history.replaceState({}, "", "/renamed"), 1500)</script>')));
    await new Promise((done) => server.listen(0, '127.0.0.1', done));
    t.after(() => { server.closeAllConnections(); server.close(); });
    const out = join(folder, 'out.mp4');
    const seen = [];
    await record(`http://127.0.0.1:${server.address().port}/old`, out, { profile: join(folder, 'browser'),
      isProfile: (found) => seen.push(new URL(found).pathname) > 0, linkedin: false, wait: 8000 });
    await access(out);
    assert.equal(seen.at(-1), '/renamed', 'the address that is recorded is checked too');
  });
