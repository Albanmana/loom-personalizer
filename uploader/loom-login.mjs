// Opens a browser window on Loom's sign-in page and saves the session once Loom accepts it.
// The browser uses a throwaway profile and is driven through a private pipe, so no network port is opened.
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, rename, rm, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { pathToFileURL } from 'node:url';
import { checkLoomSession } from './loom-upload.mjs';

const LOGIN = 'https://www.loom.com/login';
const WAIT = 8 * 60_000;
const BROWSERS = [
  'Google Chrome.app/Contents/MacOS/Google Chrome',
  'Brave Browser.app/Contents/MacOS/Brave Browser',
  'Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
  'Chromium.app/Contents/MacOS/Chromium',
];

export function findBrowser(env = process.env, exists = existsSync) {
  const places = [env.LP_BROWSER, ...['/Applications', join(homedir(), 'Applications')]
    .flatMap((root) => BROWSERS.map((app) => join(root, app)))];
  const found = places.find((path) => path && exists(path));
  if (!found) throw new Error('Install Google Chrome, Brave, Edge or Chromium, then run this again.');
  return found;
}

export function cookieHeader(cookies) {
  return cookies
    .filter(({ domain, path }) => ['.loom.com', 'www.loom.com', '.www.loom.com'].includes(domain)
      && (path === '/' || path === '/graphql'))
    .map(({ name, value }) => `${name}=${value}`)
    .join('; ');
}

export async function waitForSession({ getCookies, isOpen, check, pause = () => sleep(1500), now = Date.now,
  timeout = WAIT }) {
  const deadline = now() + timeout;
  let tried = '';
  let retry = 0;
  let shown = false;
  while (now() < deadline) {
    if (await isOpen()) shown = true;
    else if (shown) throw new Error('The window was closed before the Loom sign-in finished.');
    const header = cookieHeader(await getCookies());
    // Ask Loom again only when the cookies change, or now and then in case a request failed.
    if (header && (header !== tried || --retry <= 0)) {
      tried = header;
      retry = 10;
      try {
        await check(header);
        return header;
      } catch {
        // Not signed in yet.
      }
    }
    await pause();
  }
  throw new Error('The Loom sign-in took too long. Run this again.');
}

export async function saveSession(file, header) {
  await mkdir(dirname(file), { recursive: true, mode: 0o700 });
  const temporary = file + '.new';
  await rm(temporary, { force: true });
  await writeFile(temporary, header + '\n', { flag: 'wx', mode: 0o600 });
  await rename(temporary, file);
}

// Chrome's --remote-debugging-pipe protocol: JSON messages separated by a NUL byte.
export function cdpClient(input, output) {
  const waiting = new Map();
  let buffered = '';
  let last = 0;
  let closed = false;
  const close = () => {
    closed = true;
    for (const { reject } of waiting.values()) reject(new Error('The browser closed.'));
    waiting.clear();
  };
  output.setEncoding('utf8');
  output.on('data', (data) => {
    const parts = (buffered + data).split('\0');
    buffered = parts.pop();
    for (const part of parts) {
      const message = JSON.parse(part);
      const request = waiting.get(message.id);
      if (!request) continue;
      waiting.delete(message.id);
      if (message.error) request.reject(new Error(message.error.message));
      else request.resolve(message.result);
    }
  });
  output.on('end', close);
  output.on('error', close);
  input.on('error', close);
  return {
    send(method, params = {}, sessionId) {
      if (closed) return Promise.reject(new Error('The browser closed.'));
      return new Promise((resolve, reject) => {
        waiting.set(++last, { resolve, reject });
        input.write(JSON.stringify({ id: last, method, params, sessionId }) + '\0');
      });
    },
  };
}

export function openBrowser(executable, profile, url, extra = []) {
  const child = spawn(executable, ['--remote-debugging-pipe', `--user-data-dir=${profile}`, '--no-first-run',
    '--no-default-browser-check', ...extra, url], { stdio: ['ignore', 'ignore', 'ignore', 'pipe', 'pipe'] });
  const client = cdpClient(child.stdio[3], child.stdio[4]);
  const exited = new Promise((resolve) => {
    child.once('exit', resolve);
    child.once('error', resolve);
  });
  return {
    client,
    async close() {
      await Promise.race([client.send('Browser.close').catch(() => {}), sleep(3000)]);
      await Promise.race([exited, sleep(5000)]);
      if (child.exitCode === null) child.kill('SIGKILL');
      await exited;
    },
  };
}

export async function login(cookieFile) {
  const executable = findBrowser();
  const profile = join(dirname(cookieFile), 'login-browser');
  await rm(profile, { recursive: true, force: true });
  await mkdir(profile, { recursive: true, mode: 0o700 });
  const browser = openBrowser(executable, profile, LOGIN);
  const { client } = browser;
  try {
    const header = await waitForSession({
      getCookies: async () => (await client.send('Storage.getCookies')).cookies,
      // On a Mac, closing the last window leaves the browser running without any page.
      isOpen: async () => (await client.send('Target.getTargets')).targetInfos.some((t) => t.type === 'page'),
      check: (value) => checkLoomSession(value),
    });
    await saveSession(cookieFile, header);
  } finally {
    await browser.close();
    // The profile holds the sign-in, so none of it is kept.
    await rm(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 300 });
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [cookieFile] = process.argv.slice(2);
  try {
    if (!cookieFile) throw new Error('Give the file where the Loom session must be saved.');
    await login(cookieFile);
    console.log('Loom session accepted and saved (not shown).');
  } catch (error) {
    console.error(`No Loom session saved. ${error.message}`);
    process.exitCode = 1;
  }
}
