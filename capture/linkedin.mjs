// Records the scroll of a LinkedIn profile in this package's own Chrome profile, signed in by the user.
// Used for every lead by the local worker, before anything paid. It never works around a sign-in or security check.
import { execFile } from 'node:child_process';
import { access, mkdir, mkdtemp, rename, rm, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { pathToFileURL } from 'node:url';
import { promisify } from 'node:util';
import { findBrowser, openBrowser } from '../uploader/loom-login.mjs';

const WIDTH = 1600;
const HEIGHT = 900;
const FPS = 30;
// A trackpad flick: the finger speeds up fast, then the page glides and slows down by itself.
const flick = (start, length, from, to) => [[0, 0], [0.08, 0.12], [0.17, 0.40], [0.29, 0.66], [0.46, 0.84],
  [0.68, 0.95], [1, 1]].map(([t, p]) => [+(start + t * length).toFixed(3), +(from + p * (to - from)).toFixed(4)]);
// [seconds, position as a share of the distance]: swipes with real pauses, one small swipe back, then to the top.
// 13.4 s, so a 16 s intro is covered with the 1.2x slow-down the video fitting allows.
const MOTION = [[0, 0], ...flick(0.9, 1.4, 0, 0.30), ...flick(3.0, 0.8, 0.30, 0.41), ...flick(4.2, 1.7, 0.41, 0.75),
  ...flick(6.8, 0.6, 0.75, 0.69), ...flick(8.0, 1.8, 0.69, 1), ...flick(10.8, 2.1, 1, 0), [13.4, 0]];
const HEADING = '.pv-text-details__left-panel h1, h1.top-card-layout__title, main h1, main h2';
const CHALLENGE = 'iframe[title*="captcha" i], iframe[src*="captcha" i], form[action*="/checkpoint/challenge"], '
  + '#captcha-internal';
const SIGN_IN = '.authwall, #authwall, form[action*="/login-submit"], input[name="session_key"], '
  + 'input[name="session_password"]';
// Private conversations must never end up in a public video.
// The current layout keeps messaging in a shadow root, which page styles reach only through its host.
const HIDDEN = '#interop-outlet, [data-testid="interop-shadowdom"], #msg-overlay, .msg-overlay-container '
  + '{ display: none !important }';
const RUN = 'python3 studio.py linkedin';
// LinkedIn can keep the profile's address and show this instead of the person.
const UNAVAILABLE = /^(?:(?:this )?profile (?:is not|isn't|isn’t) available|page not found)[.!]?$/i;
const URL_ERROR = 'Use a LinkedIn /in/ profile URL over HTTPS.';

export function profileUrl(value) {
  try {
    const url = new URL(String(value).trim());
    const profile = /^\/in\/([^/]+)\/?$/.exec(url.pathname);
    if (url.protocol !== 'https:' || !['linkedin.com', 'www.linkedin.com'].includes(url.hostname)
      || url.username || url.password || url.port || !profile) throw new Error();
    const name = decodeURIComponent(profile[1]);
    if (!/^[-._~\p{L}\p{M}\p{N}]+$/u.test(name) || name === '.' || name === '..') throw new Error();
    return `https://www.linkedin.com/in/${encodeURIComponent(name)}/`;
  } catch {
    throw new Error(URL_ERROR);
  }
}

// A smooth curve through the points without overshooting: speed changes gradually, equal values make a pause.
function curve(points) {
  const slope = points.slice(1).map(([t, v], i) => (v - points[i][1]) / (t - points[i][0]));
  const tangent = points.map((_, i) => {
    const before = slope[i - 1];
    const after = slope[i];
    if (i === 0 || i === points.length - 1 || before * after <= 0) return 0;
    const h0 = points[i][0] - points[i - 1][0];
    const h1 = points[i + 1][0] - points[i][0];
    return (3 * (h0 + h1)) / ((2 * h1 + h0) / before + (h1 + 2 * h0) / after);
  });
  return (time) => {
    let i = 0;
    while (i < points.length - 2 && time > points[i + 1][0]) i += 1;
    const [t0, v0] = points[i];
    const [t1, v1] = points[i + 1];
    const h = t1 - t0;
    const x = Math.min(1, Math.max(0, (time - t0) / h));
    return (2 * x ** 3 - 3 * x ** 2 + 1) * v0 + (x ** 3 - 2 * x ** 2 + x) * h * tangent[i]
      + (-2 * x ** 3 + 3 * x ** 2) * v1 + (x ** 3 - x ** 2) * h * tangent[i + 1];
  };
}

export function scrollOffsets(travel) {
  const at = curve(MOTION);
  return Array.from({ length: Math.round(MOTION.at(-1)[0] * FPS) }, (_, frame) =>
    Math.round(travel * at((frame + 1) / FPS)));
}

export function blockReason({ url, status, challenge, signIn }) {
  const path = new URL(url).pathname.toLowerCase();
  if (challenge || /^\/(checkpoint|challenge|captcha)/.test(path)) {
    return `LinkedIn shows a security check. Run \`${RUN}\` and complete it yourself in that window.`;
  }
  if (signIn || status === 401 || /^\/(login|uas\/login|authwall|signup)/.test(path)) {
    return `LinkedIn asks you to sign in. Run \`${RUN}\` and sign in in that window.`;
  }
  if ([403, 429, 999].includes(status)) return `LinkedIn denied access to this profile (HTTP ${status}).`;
  if (status >= 400) return `LinkedIn did not load this profile (HTTP ${status}).`;
  return null;
}

// Runs inside the page. The current layout scrolls its main area instead of the whole document.
function scroller() {
  return [document.scrollingElement, ...document.querySelectorAll('main')].find((node) =>
    node.scrollHeight - node.clientHeight >= 20
    && (node === document.scrollingElement || /auto|scroll/.test(getComputedStyle(node).overflowY)));
}

// Runs inside the page: is any part of messaging still on screen, including inside shadow roots?
function messagingShown(root = document) {
  return [...root.querySelectorAll('*')].some((node) => (node.shadowRoot && messagingShown(node.shadowRoot))
    || (String(node.getAttribute('class')).includes('msg-overlay') && node.checkVisibility({ checkVisibilityCSS: true })
      && node.getBoundingClientRect().height > 0 && node.getBoundingClientRect().top < innerHeight));
}

// Runs inside the page.
function inspect(heading, challenge, signIn, box) {
  const shown = (selector) => [...document.querySelectorAll(selector)]
    .filter((node) => node.checkVisibility({ checkVisibilityCSS: true }));
  return {
    url: location.href,
    status: performance.getEntriesByType('navigation')[0]?.responseStatus ?? 0,
    loaded: document.readyState !== 'loading',
    challenge: shown(challenge).length > 0,
    signIn: shown(signIn).length > 0,
    name: shown(heading)[0]?.innerText.trim() ?? '',
    travel: box ? Math.min(box.scrollHeight - box.clientHeight, innerHeight * 2.4) : 0,
  };
}

async function openPage(client) {
  let target;
  for (let tries = 0; !target; tries += 1) {
    const { targetInfos } = await client.send('Target.getTargets');
    target = targetInfos.find((t) => t.type === 'page');
    if (!target) {
      if (tries > 100) throw new Error('The capture browser did not open a page.');
      await sleep(100);
    }
  }
  const { sessionId } = await client.send('Target.attachToTarget', { targetId: target.targetId, flatten: true });
  const send = (method, params) => client.send(method, params, sessionId);
  const evaluate = async (expression) => {
    const { result, exceptionDetails } = await send('Runtime.evaluate',
      { expression, returnByValue: true, awaitPromise: true });
    if (exceptionDetails) throw new Error('The page could not be read.');
    return result.value;
  };
  return { send, evaluate };
}

export async function record(pageUrl, output, { profile, isProfile, wait = 30_000 }) {
  await mkdir(profile, { recursive: true, mode: 0o700 });
  await mkdir(dirname(output), { recursive: true });
  const folder = await mkdtemp(join(dirname(output), '.capture-'));
  const browser = openBrowser(findBrowser(), profile, 'about:blank',
    ['--headless=new', `--window-size=${WIDTH},${HEIGHT}`, '--hide-scrollbars', '--lang=en-US']);
  try {
    const { send, evaluate } = await openPage(browser.client);
    await send('Emulation.setDeviceMetricsOverride',
      { width: WIDTH, height: HEIGHT, deviceScaleFactor: 1, mobile: false });
    const { errorText } = await send('Page.navigate', { url: pageUrl });
    if (errorText) throw new Error(`The browser could not open this LinkedIn profile (${errorText}).`);
    const read = `(${inspect})(${[HEADING, CHALLENGE, SIGN_IN].map((s) => JSON.stringify(s))}, (${scroller})())`;
    const refuseIfMessagingShows = async () => {
      if (await evaluate(`(${messagingShown})()`)) {
        throw new Error('Nothing was recorded: the LinkedIn message list could not be hidden. '
          + 'LinkedIn has probably changed its page.');
      }
    };
    const check = (state) => {
      const reason = state.url.startsWith('about:') ? null : blockReason(state);
      if (reason) throw new Error(reason);
    };
    let state;
    for (const deadline = Date.now() + wait; ; await sleep(500)) {
      try {
        state = await evaluate(read);
      } catch (error) {
        // LinkedIn can redirect after the first load, which drops the page being read.
        if (Date.now() > deadline) throw error;
        continue;
      }
      check(state);
      if (UNAVAILABLE.test(state.name)) throw new Error('LinkedIn did not display an available profile.');
      if (state.loaded && state.name && state.travel >= 20) break;
      if (Date.now() > deadline) {
        throw new Error(state.name ? 'The LinkedIn profile did not have enough content to scroll.'
          : 'No visible LinkedIn profile heading appeared.');
      }
    }
    if (!isProfile(state.url)) throw new Error('LinkedIn redirected away from the profile page.');
    await evaluate(`(async () => {
      // A constructed style sheet: the site's content security policy ignores an added <style> tag.
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(${JSON.stringify(HIDDEN)});
      document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
      (${scroller})().scrollTo({ top: 0, behavior: 'instant' });
      await Promise.race([Promise.all([document.fonts.ready, ...[...document.images]
        .filter((i) => i.getBoundingClientRect().top < innerHeight && i.currentSrc)
        .map((i) => i.decode().catch(() => {}))]), new Promise((r) => setTimeout(r, 2000))]);
    })()`);
    // Read through the page once so that its sections load, then give the top a moment before recording.
    await evaluate(`(async () => {
      const box = (${scroller})();
      const pause = (ms) => new Promise((done) => setTimeout(done, ms));
      for (let step = 1; step <= 5; step += 1) {
        box.scrollTo({ top: innerHeight * 2.4 * step / 5, behavior: 'instant' });
        await pause(700);
      }
      box.scrollTo({ top: 0, behavior: 'instant' });
      await pause(2500);
    })()`);
    await refuseIfMessagingShows();
    const { travel } = await evaluate(read);
    let previous;
    let picture;
    for (const [number, offset] of scrollOffsets(travel).entries()) {
      if (offset !== previous) {
        await evaluate(`new Promise((done) => { (${scroller})().scrollTo({ top: ${offset}, behavior: 'instant' });
          requestAnimationFrame(() => requestAnimationFrame(done)); })`);
        picture = Buffer.from((await send('Page.captureScreenshot', { format: 'jpeg', quality: 95 })).data, 'base64');
        previous = offset;
      }
      await writeFile(join(folder, `frame-${String(number).padStart(4, '0')}.jpg`), picture);
    }
    const end = await evaluate(read);
    check(end);
    await refuseIfMessagingShows();
    const path = (value) => value.split(/[?#]/)[0];
    if (path(end.url) !== path(state.url)) throw new Error('LinkedIn left the profile during the capture.');
    const movie = join(folder, 'capture.mp4');
    await promisify(execFile)('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-nostdin', '-framerate', String(FPS),
      '-i', join(folder, 'frame-%04d.jpg'), '-an', '-c:v', 'libx264', '-threads', '3', '-preset', 'veryfast',
      '-crf', '19', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', movie], { timeout: 60_000 });
    await rename(movie, output);
  } finally {
    await browser.close();
    await rm(folder, { recursive: true, force: true });
  }
}

export async function capture(url, output, home) {
  const wanted = profileUrl(url);
  await access(join(home, 'signed-in')).catch(() => {
    throw new Error(`LinkedIn is not connected yet. Run \`${RUN}\` and sign in.`);
  });
  const isProfile = (found) => {
    try {
      return Boolean(profileUrl(found.split(/[?#]/)[0]));
    } catch {
      return false;
    }
  };
  await record(wanted, output, { profile: join(home, 'browser'), isProfile });
  return wanted;
}

// A visible window where the user signs in. The sign-in stays in this package's own browser profile.
export async function signIn(home, timeout = 8 * 60_000) {
  const profile = join(home, 'browser');
  await mkdir(profile, { recursive: true, mode: 0o700 });
  await rm(join(home, 'signed-in'), { force: true });
  const browser = openBrowser(findBrowser(), profile, 'https://www.linkedin.com/login');
  try {
    let shown = false;
    for (const deadline = Date.now() + timeout; Date.now() < deadline; await sleep(1000)) {
      const { targetInfos } = await browser.client.send('Target.getTargets');
      const pages = targetInfos.filter((t) => t.type === 'page');
      if (pages.some((t) => /^https:\/\/www\.linkedin\.com\/feed([/?#]|$)/.test(t.url))) {
        await writeFile(join(home, 'signed-in'), '', { mode: 0o600 });
        return;
      }
      if (pages.length) shown = true;
      else if (shown) throw new Error('The window was closed before the LinkedIn sign-in finished.');
    }
    throw new Error('The LinkedIn sign-in took too long. Run this again.');
  } finally {
    await browser.close();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [action, home, url, output] = process.argv.slice(2);
  try {
    if (action === 'login' && home) {
      await signIn(home);
      console.log('LinkedIn is connected in this package\'s own browser.');
    } else if (action === 'capture' && output) {
      console.log(JSON.stringify({ url: await capture(url, output, home) }));
    } else {
      throw new Error('Use: linkedin.mjs login <folder> | capture <folder> <profile url> <out.mp4>');
    }
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
