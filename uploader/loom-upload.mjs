import { createHash, randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import { constants } from 'node:fs';
import { mkdir, mkdtemp, open, rm, writeFile } from 'node:fs/promises';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { basename, extname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const LOOM = 'https://www.loom.com';
const STORAGE = 'https://object-store.atlassian.com';
const GRAPHQL = LOOM + '/graphql';
const LIBRARY = LOOM + '/looms/videos';
const CHUNK_SIZE = 8 * 1024 * 1024;
const MAX_SIZE = 4_000_000_000;
// The package keeps its own session and token, apart from any other install on this Mac.
const CONFIG = process.env.LOOM_CONFIG_DIR || fileURLToPath(new URL('../data/loom', import.meta.url));
export const DEFAULT_COOKIE_FILE = join(CONFIG, 'session.cookie');
const TOKEN_FILE = join(CONFIG, 'api.token');
const UUID = /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/i;
const IDENTITY = 'query LoomUploadIdentity { me { id } }';

const CREDENTIALS = `mutation CreateFileUploadCredentials($sessionId: ID) {
  createFileUploadCredentials(sessionId: $sessionId) {
    __typename
    ... on CreateFileUploadCredentialsPayload {
      sessionId
      credentials {
        __typename
        ... on ObjectStoreInfo {
          hostname
          bucket
          workspaceType
          siteId
          activationId
          token { value expiration }
        }
      }
    }
  }
}`;

const PUBLISH = `mutation CompleteVideoFileUpload(
  $keyPath: String!, $folderId: String, $spaceId: String, $fileName: String,
  $privacy: VideoPrivacyProperty, $videoProperties: InputVideoProperties!
) {
  video: completeVideoFileUpload(
    keyPath: $keyPath, folderId: $folderId, spaceId: $spaceId, fileName: $fileName,
    privacy: $privacy, videoProperties: $videoProperties
  ) { id __typename }
}`;

class UploadError extends Error {
  constructor(code, message, details = {}) {
    super(message);
    this.code = code;
    Object.assign(this, details);
  }
}

function normaliseCookie(value) {
  const cookie = String(value ?? '').trim().replace(/^cookie:\s*/i, '');
  if (!cookie) throw new UploadError('missing_cookie', 'Configure the Loom session cookie on this machine.');
  if (/[^\x20-\x7e]/.test(cookie) || !cookie.includes('=')) {
    throw new UploadError('invalid_cookie', 'Expected a single Loom Cookie header value.');
  }
  return cookie;
}

function requirePublicVisibility(privacy) {
  if (privacy !== 'public') {
    throw new UploadError('explicit_public_visibility_required', 'Select public visibility to create a public Loom link.');
  }
}

async function readPrivateFile(path, onMissing) {
  let file;
  try {
    file = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const info = await file.stat();
    if (!info.isFile() || info.size > 32_768 || (info.mode & 0o077) !== 0
        || (process.getuid && info.uid !== process.getuid())) {
      throw new UploadError('unsafe_secret_permissions', 'Use a private, user-owned secret file with mode 0600.');
    }
    return (await file.readFile('utf8')).trim();
  } catch (error) {
    if (error instanceof UploadError) throw error;
    if (error.code === 'ENOENT') return onMissing();
    throw new UploadError('unreadable_secret', 'The configured secret file cannot be read.');
  } finally {
    await file?.close();
  }
}

export async function readLoomCookie(path = DEFAULT_COOKIE_FILE) {
  return normaliseCookie(await readPrivateFile(path, () => ''));
}

async function apiTokenFromDisk() {
  return readPrivateFile(TOKEN_FILE, async () => {
    await mkdir(CONFIG, { recursive: true, mode: 0o700 });
    try {
      await writeFile(TOKEN_FILE, randomBytes(32).toString('hex') + '\n', { flag: 'wx', mode: 0o600 });
    } catch (error) {
      if (error.code !== 'EEXIST') throw new UploadError('api_token_write_failed', 'The local API token could not be saved.');
    }
    return readPrivateFile(TOKEN_FILE, () => '');
  });
}

async function request(fetchImpl, url, options, stage, expected, parseJson = true) {
  let response;
  try {
    options.signal?.throwIfAborted();
    const timeout = AbortSignal.timeout(90_000);
    response = await fetchImpl(url, {
      ...options,
      redirect: 'error',
      signal: options.signal ? AbortSignal.any([options.signal, timeout]) : timeout,
    });
  } catch {
    if (options.signal?.aborted) throw new UploadError('upload_cancelled', 'The upload was cancelled by the caller.', { stage });
    throw new UploadError('upstream_connection_failed', 'The upstream request did not complete.', { stage });
  }
  if (response.status !== expected) {
    await response.body?.cancel().catch(() => {});
    throw new UploadError('upstream_http_error', 'The upstream service rejected the request.',
      { stage, upstreamStatus: response.status });
  }
  if (!parseJson) {
    await response.body?.cancel().catch(() => {});
    return;
  }
  try {
    return await response.json();
  } catch {
    throw new UploadError('invalid_upstream_response', 'The upstream service did not return the expected JSON.', { stage });
  }
}

function storageContract(credentials, expectedSessionId) {
  const info = credentials?.credentials;
  if (info?.__typename !== 'ObjectStoreInfo') {
    throw new UploadError('unsupported_storage_type', 'Loom did not provide Atlassian object storage credentials.',
      { stage: 'credentials' });
  }
  let origin;
  try {
    origin = new URL(info.hostname);
  } catch {
    throw new UploadError('unexpected_storage_origin', 'Loom returned an unexpected upload origin.', { stage: 'credentials' });
  }
  if (origin.href !== STORAGE + '/') {
    throw new UploadError('unexpected_storage_origin', 'Loom returned an unexpected upload origin.', { stage: 'credentials' });
  }
  if (credentials.sessionId !== expectedSessionId || info.bucket !== 'loom-media-content' || info.workspaceType !== 'loom'
      || !UUID.test(info.siteId) || !UUID.test(info.activationId) || typeof info.token?.value !== 'string' || !info.token.value) {
    throw new UploadError('invalid_storage_credentials', 'Loom returned incomplete upload credentials.', { stage: 'credentials' });
  }
  const route = { entityType: 'site', entityId: info.siteId };
  return {
    base: STORAGE + `/${info.bucket}/upload/loom/${info.siteId}/${info.activationId}/multipart`,
    token: info.token.value,
    routing: Buffer.from(JSON.stringify(route)).toString('base64url'),
  };
}

async function readPart(file, size, position) {
  const chunk = Buffer.allocUnsafe(size);
  let offset = 0;
  while (offset < size) {
    const { bytesRead } = await file.read(chunk, offset, size - offset, position + offset);
    if (!bytesRead) throw new UploadError('truncated_mp4', 'The file ended during transfer.', { stage: 'part' });
    offset += bytesRead;
  }
  return chunk;
}

export async function uploadMp4(filePath, { cookie, title, privacy, signal, fetchImpl = fetch }) {
  requirePublicVisibility(privacy);
  if (extname(filePath).toLowerCase() !== '.mp4') throw new UploadError('invalid_mp4', 'Choose a file with the .mp4 extension.');
  const fileName = (title ?? basename(filePath, extname(filePath))).trim();
  if (!fileName || fileName.length > 250) throw new UploadError('invalid_title', 'Use a title of 1 to 250 characters.');
  const sessionCookie = normaliseCookie(cookie);
  const file = await open(filePath, constants.O_RDONLY | constants.O_NONBLOCK).catch(() => {
    throw new UploadError('unreadable_mp4', 'The MP4 could not be opened.');
  });
  let uploadId;
  let stage = 'identity';
  async function graphql(operationName, query, variables) {
    const result = await request(fetchImpl, GRAPHQL, {
      method: 'POST',
      headers: { cookie: sessionCookie, 'content-type': 'application/json', origin: LOOM, referer: LIBRARY },
      body: JSON.stringify({ operationName, query, variables }),
      signal,
    }, stage, 200);
    if (!result?.data || result.errors?.length) {
      const upstreamCode = result?.errors?.[0]?.extensions?.code;
      const details = { stage };
      if (['UNAUTHENTICATED', 'FORBIDDEN', 'BAD_USER_INPUT', 'GRAPHQL_VALIDATION_FAILED', 'INTERNAL_SERVER_ERROR']
        .includes(upstreamCode)) details.upstreamCode = upstreamCode;
      throw new UploadError('loom_graphql_error', 'Loom did not accept the GraphQL request.', details);
    }
    return result.data;
  }
  try {
    const info = await file.stat();
    if (!info.isFile() || info.size < 1) throw new UploadError('invalid_mp4', 'Choose a nonempty MP4 file.');
    if (info.size > MAX_SIZE) throw new UploadError('mp4_too_large', 'The MP4 exceeds the 4 GB upload limit.');
    const identity = await graphql('LoomUploadIdentity', IDENTITY, {});
    const ownerId = String(identity.me?.id ?? '');
    if (!/^\d{1,20}$/.test(ownerId)) {
      throw new UploadError('loom_session_unavailable', 'Loom did not return an authenticated account ID.', { stage });
    }

    stage = 'credentials';
    const sessionId = randomUUID();
    const result = await graphql('CreateFileUploadCredentials', CREDENTIALS, { sessionId });
    const storage = storageContract(result.createFileUploadCredentials, sessionId);
    const keyPath = sessionId + '-' + randomUUID() + '.mp4';
    const objectId = 'uploads-' + ownerId + '-' + keyPath;
    const routingHeader = { 'x-loom-entity-routing': storage.routing };
    function storageRequest(path, options, status, parseJson) {
      const url = new URL(storage.base + path);
      if (!path) {
        url.searchParams.set('objectId', objectId);
        url.searchParams.set('shouldOverwrite', 'false');
        url.searchParams.set('contentType', 'video/mp4');
      }
      url.searchParams.set('authToken', storage.token);
      return request(fetchImpl, url, { ...options, signal, headers: { ...routingHeader, ...options.headers } },
        stage, status, parseJson);
    }

    stage = 'open_transfer';
    const opened = await storageRequest('', { method: 'POST', headers: { 'x-content-checksum-type': 'SHA256' } }, 200);
    if (!UUID.test(opened?.uploadId) || opened.objectId !== objectId) {
      throw new UploadError('invalid_transfer_response', 'The storage service returned an unexpected upload reference.', { stage });
    }
    uploadId = opened.uploadId;
    stage = 'part';
    for (let position = 0, partNumber = 1; position < info.size; position += CHUNK_SIZE, partNumber++) {
      const part = await readPart(file, Math.min(CHUNK_SIZE, info.size - position), position);
      await storageRequest('/' + uploadId + '/parts', {
        method: 'PUT',
        headers: {
          'content-type': 'application/octet-stream',
          'x-part-number': String(partNumber),
          'x-content-checksum-type': 'SHA256',
          'x-content-checksum': createHash('sha256').update(part).digest('base64'),
        },
        body: part,
      }, 201, false);
    }
    stage = 'close_transfer';
    const stored = await storageRequest('/' + uploadId + '/complete', {
      method: 'POST',
      headers: { 'x-expected-size': String(info.size) },
    }, 202);
    if (stored?.uploadId !== uploadId || stored.objectId !== objectId || stored.size !== info.size) {
      throw new UploadError('invalid_transfer_response', 'The completed object did not match the file sent.', { stage });
    }
    stage = 'publish';
    const published = await graphql('CompleteVideoFileUpload', PUBLISH, {
      keyPath,
      folderId: null,
      spaceId: null,
      fileName,
      videoProperties: { file_size: info.size, file_anonymous_id: randomUUID() },
      privacy,
    });
    const videoId = published.video?.id;
    if (typeof videoId !== 'string' || !/^[a-f0-9]{32}$/i.test(videoId)) {
      throw new UploadError('invalid_video_id', 'Loom did not return a valid video ID.', { stage });
    }
    return { videoId, url: LOOM + '/share/' + videoId, privacy };
  } catch (error) {
    const failure = error instanceof UploadError ? error
      : new UploadError('upload_failed', 'The file could not be uploaded.', { stage });
    if (uploadId) failure.uploadId = uploadId;
    if (stage === 'publish') failure.outcomeUnknown = true;
    throw failure;
  } finally {
    await file.close();
  }
}

export async function checkLoomSession(cookie, fetchImpl = fetch) {
  const result = await request(fetchImpl, GRAPHQL, {
    method: 'POST',
    headers: { cookie: normaliseCookie(cookie), 'content-type': 'application/json', origin: LOOM, referer: LIBRARY },
    body: JSON.stringify({ operationName: 'LoomUploadIdentity', query: IDENTITY, variables: {} }),
  }, 'identity', 200);
  if (!/^\d{1,20}$/.test(String(result?.data?.me?.id ?? ''))) {
    throw new UploadError('loom_session_unavailable', 'Loom did not return an authenticated account ID.',
      { stage: 'identity' });
  }
}

function errorBody(error) {
  if (!(error instanceof UploadError)) return { error: 'internal_error', message: 'The operation failed locally.' };
  const { code, message, stage, upstreamStatus, upstreamCode, uploadId, outcomeUnknown } = error;
  return { error: code, message, stage, upstreamStatus, upstreamCode, uploadId, outcomeUnknown };
}

function matchesToken(value, token) {
  return typeof value === 'string' && timingSafeEqual(
    createHash('sha256').update(value).digest(),
    createHash('sha256').update('Bearer ' + token).digest(),
  );
}

function respond(res, status, payload) {
  res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
  res.end(JSON.stringify(payload));
}

async function receiveFile(req, path) {
  const file = await open(path, 'wx', 0o600);
  let received = 0;
  try {
    for await (const chunk of req.iterator({ destroyOnReturn: false })) {
      received += chunk.length;
      if (received > MAX_SIZE) throw new UploadError('mp4_too_large', 'The MP4 exceeds the 4 GB upload limit.');
      await file.writeFile(chunk);
    }
  } finally {
    await file.close();
  }
}

export function createUploadServer({ apiToken, getCookie, fetchImpl = fetch }) {
  if (!apiToken) throw new UploadError('missing_api_token', 'Configure a local API token before starting the server.');
  let activeUpload = false;
  return createServer(async (req, res) => {
    let directory;
    let reserved = false;
    let status;
    let body;
    try {
      const { pathname, searchParams } = new URL(req.url, 'http://127.0.0.1');
      if (req.method === 'GET' && pathname === '/health') return respond(res, 200, { ok: true });
      if (req.method !== 'POST' || pathname !== '/upload') return respond(res, 404, { error: 'not_found' });
      if (!matchesToken(req.headers.authorization, apiToken)) {
        res.setHeader('connection', 'close');
        return respond(res, 401, { error: 'unauthorized' });
      }
      const mime = req.headers['content-type']?.split(';')[0].trim().toLowerCase();
      if (mime !== 'video/mp4' && mime !== 'application/octet-stream') {
        res.setHeader('connection', 'close');
        return respond(res, 415, { error: 'expected_binary_mp4' });
      }
      if (Number(req.headers['content-length']) > MAX_SIZE) {
        throw new UploadError('mp4_too_large', 'The MP4 exceeds the 4 GB upload limit.');
      }
      const privacy = searchParams.get('privacy');
      requirePublicVisibility(privacy);
      if (activeUpload) throw new UploadError('upload_in_progress', 'Wait for the current upload to finish.');
      reserved = activeUpload = true;
      const controller = new AbortController();
      res.once('close', () => {
        if (!res.writableFinished) controller.abort();
      });
      const cookie = await getCookie();
      directory = await mkdtemp(join(tmpdir(), 'loom-upload-api-'));
      const path = join(directory, 'video.mp4');
      await receiveFile(req, path);
      const title = searchParams.get('title') ?? 'Loom upload';
      body = await uploadMp4(path, { cookie, title, privacy, signal: controller.signal, fetchImpl });
      status = 201;
    } catch (error) {
      const code = error instanceof UploadError ? error.code : null;
      status = error instanceof UploadError && error.stage ? 502 : 500;
      if (['invalid_mp4', 'invalid_title', 'explicit_public_visibility_required'].includes(code)) status = 400;
      if (error instanceof UploadError && error.outcomeUnknown) status = 409;
      if (code === 'mp4_too_large') status = 413;
      if (code === 'upload_in_progress') status = 429;
      if (['missing_cookie', 'invalid_cookie', 'unreadable_secret', 'unsafe_secret_permissions'].includes(code)) status = 503;
      if (!req.complete && !res.destroyed) res.setHeader('connection', 'close');
      body = errorBody(error);
    } finally {
      if (directory) await rm(directory, { recursive: true, force: true }).catch(() => {});
      if (reserved) activeUpload = false;
    }
    if (!res.destroyed) respond(res, status, body);
  });
}

async function main() {
  const { values, positionals } = parseArgs({
    allowPositionals: true,
    options: {
      'cookie-file': { type: 'string' },
      title: { type: 'string' },
      port: { type: 'string' },
      public: { type: 'boolean' },
    },
  });
  const [command, file, ...extra] = positionals;
  const cookieFile = values['cookie-file'] ?? DEFAULT_COOKIE_FILE;
  if (command === 'upload' && file && extra.length === 0) {
    const privacy = values.public ? 'public' : undefined;
    requirePublicVisibility(privacy);
    const cookie = await readLoomCookie(cookieFile);
    console.log(JSON.stringify(await uploadMp4(file, { cookie, title: values.title, privacy })));
  } else if (command === 'serve' && !file) {
    const requestedPort = values.port ?? '8792';
    const port = Number(requestedPort);
    if (!/^\d+$/.test(requestedPort) || !Number.isInteger(port) || port < 1 || port > 65535) {
      throw new UploadError('invalid_port', 'Choose a port from 1 to 65535.');
    }
    const server = createUploadServer({ apiToken: await apiTokenFromDisk(), getCookie: () => readLoomCookie(cookieFile) });
    server.once('error', () => {
      console.error(JSON.stringify({ error: 'listen_failed', message: 'The local API could not bind the requested port.' }));
      process.exitCode = 1;
    });
    server.listen(port, '127.0.0.1', () => console.log('Loom upload API listening on http://127.0.0.1:' + port));
  } else {
    throw new UploadError('usage', 'Use upload <file.mp4> --public [--title <title>] or serve [--port <port>]. Optionally pass --cookie-file <path>.');
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await main().catch((error) => {
    console.error(JSON.stringify(errorBody(error)));
    process.exitCode = 1;
  });
}
