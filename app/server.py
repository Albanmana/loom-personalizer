"""Local API for n8n. Listens on 127.0.0.1 only."""
import json
import queue
import re
import subprocess
import threading
import uuid
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from app import config, media, pipeline, providers, template

MAX_BODY = 5 * 1024 * 1024
CAPTURE_TIMEOUT = 300  # Seconds. A heavy profile takes a little over two minutes.


class Refused(Exception):
    def __init__(self, status, detail):
        self.status, self.detail = status, detail


class App:
    def __init__(self, *, template_dir, jobs_dir, loom_dir, worker_token, download, lipsync, capture, page_dir=None):
        self.capture, self.capturing = capture, threading.Lock()
        self.page_dir = Path(page_dir or Path(jobs_dir).parent / 'page-recordings')
        self.template_dir, self.jobs_dir, self.loom_dir = Path(template_dir), Path(jobs_dir), Path(loom_dir)
        self.worker_token = worker_token
        self.download, self.lipsync = download, lipsync
        self.lock = threading.Lock()
        self.renders = queue.Queue()
        self._fail_interrupted_jobs()
        threading.Thread(target=self._render_forever, daemon=True).start()

    # --- jobs -------------------------------------------------------------
    def _fail_interrupted_jobs(self):
        """A job cut off by a restart would otherwise stay "rendering" and n8n would wait forever."""
        for path in self.jobs_dir.glob('*/job.json'):
            state = json.loads(path.read_text())
            if state.get('status') in ('queued', 'rendering'):
                self._save(path.parent, {**state, 'status': 'failed',
                                         'error': 'The local service restarted during this render. Do not submit the lead again, '
                                                  'which would pay twice: ask Claude Code to resume this job.'})

    def _job_dir(self, job_id):
        folder = self.jobs_dir / job_id
        if not re.fullmatch(r'[a-f0-9]{32}', job_id) or not (folder / 'job.json').is_file():
            raise Refused(404, 'Job not found.')
        return folder

    def state(self, job_id):
        return json.loads((self._job_dir(job_id) / 'job.json').read_text())

    def _save(self, folder, state):
        temporary = folder / 'job.tmp'
        temporary.write_text(json.dumps(state))
        temporary.replace(folder / 'job.json')

    def record_page(self, body):
        """Record the lead's page. One at a time: captures share one signed-in browser profile."""
        url = str(body.get('url', '')).strip()
        if 'script' in body:  # Refuse a mistake before the profile is visited.
            try:
                template.fill(str(body['script'] or ''), body.get('variables') or {})
            except ValueError as error:
                raise Refused(422, str(error)) from None
            if not pipeline.demo_is_approved(self.template_dir):
                raise Refused(422, 'Prepare and approve the demo before sending a lead.')
        name = uuid.uuid4().hex + '.mp4'
        out = config.private_dir(self.page_dir) / name
        try:
            with self.capturing:
                url = self.capture(url, out) or url
        except RuntimeError as error:
            raise Refused(422, str(error)) from None
        return {'pageRecording': name, 'url': url}

    def create_lead(self, body):
        url = str(body.get('url', '')).strip()
        parts = urlsplit(url)
        if url and (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password):
            raise Refused(422, 'Give a full web address starting with https://')
        values = body.get('variables') or {}
        if isinstance(values, str):  # One "name = value" per line, as typed in the n8n form.
            values = dict((k.strip(), v.strip()) for k, v in
                          (line.split('=', 1) for line in values.splitlines() if '=' in line))
        if not isinstance(values, dict):
            raise Refused(422, 'variables must be an object or "name = value" lines.')
        saved = str(body.get('pageRecording') or '').strip()
        if not saved or Path(saved).name != saved or not (self.page_dir / saved).is_file():
            raise Refused(422, f'Page recording not found. Put the file in {self.page_dir} and give its name only.')
        try:
            t = template.load(self.template_dir)
            # n8n sends the intro as edited there. Without it, the template's own intro is used.
            script = template.fill(str(body['script'] or ''), values) if 'script' in body \
                else template.render_intro(t, values)
            found = template.problems(t)
        except ValueError as error:
            raise Refused(422, str(error)) from None
        if found:
            raise Refused(422, 'The template is not ready: ' + ' '.join(found))
        if not pipeline.demo_is_approved(self.template_dir):
            raise Refused(422, 'Prepare and approve the demo before sending a lead.')
        job_id = uuid.uuid4().hex
        folder = config.private_dir(self.jobs_dir) / job_id
        folder.mkdir()
        state = {'jobId': job_id, 'status': 'created', 'url': url, 'variables': values, 'script': script,
                 'pageRecording': saved,
                 'voiceId': t['voiceId'], 'language': t.get('language', 'en'), 'speed': t.get('speed', 1)}
        self._save(folder, state)
        return state

    def start_render(self, job_id, body):
        with self.lock:
            folder, state = self._job_dir(job_id), self.state(job_id)
            if state['status'] not in ('created', 'failed'):
                return state
            audio = str(body.get('audioUrl', ''))
            if not audio and not (folder / 'voice.wav').is_file():
                raise Refused(422, 'HeyGen returned no audio URL.')
            state.update(status='queued')
            state.pop('error', None)
            self._save(folder, state)
            self.renders.put((job_id, audio))
            return state

    def _render_forever(self):
        while True:
            job_id, audio = self.renders.get()
            folder = self.jobs_dir / job_id
            try:
                state = self.state(job_id)
            except Exception:
                continue  # A removed or damaged job must not stop the ones after it.
            try:
                self._save(folder, {**state, 'status': 'rendering'})
                voice = folder / 'voice.wav'
                if not voice.is_file():
                    partial = folder / 'voice.part'
                    self.download(audio, partial)
                    partial.replace(voice)
                recording = self.page_dir / state.get('pageRecording', '')
                result = pipeline.render_lead(
                    self.template_dir, folder, lambda seconds, out: media.fit_page(recording, seconds, out),
                    self.lipsync)
                state.update(status='complete', **result,
                             videoUrl=f'http://127.0.0.1:{config.WORKER_PORT}/api/jobs/{job_id}/video')
            except Exception as error:
                message = str(error) if isinstance(error, (RuntimeError, ValueError)) else type(error).__name__
                state.update(status='failed', error=re.sub(r'https?://\S+\?\S+', '[URL]', message)[:300])
            self._save(folder, state)

def make_server(app: App, port=config.WORKER_PORT):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, payload=None, kind='application/json'):
            data = b'' if payload is None else payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            if data:
                self.send_header('content-type', kind)
            self.send_header('content-length', str(len(data)))
            self.send_header('cache-control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            length = int(self.headers.get('content-length') or 0)
            if not 0 <= length <= MAX_BODY:
                self.close_connection = True
                raise Refused(413, 'Request too large.')
            return self.rfile.read(length)

        def _json(self, raw):
            try:
                body = json.loads(raw or b'{}')
                assert isinstance(body, dict)
            except (ValueError, AssertionError):
                raise Refused(400, 'Send a JSON object.') from None
            return body

        def do_GET(self):
            self._route('GET')

        def do_POST(self):
            self._route('POST')

        def _route(self, method):
            try:
                raw = self._body()
                # A web page cannot use another host name to reach this server (DNS rebinding).
                if (self.headers.get('host') or '').split(':')[0] not in ('127.0.0.1', 'localhost'):
                    raise Refused(403, 'Unexpected host.')
                url = urlsplit(self.path)
                path = url.path
                if path == '/health':
                    return self._send(200, {'ok': True})
                if not compare_digest((self.headers.get('authorization') or '').encode(),
                                      f'Bearer {app.worker_token}'.encode()):
                    raise Refused(401, 'Missing or wrong token.')
                return self._api(method, path, raw)
            except Refused as refusal:
                self._send(refusal.status, {'detail': refusal.detail})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as error:
                self._send(500, {'detail': f'Local error ({type(error).__name__}).'})

        def _api(self, method, path, raw):
            if method == 'POST' and path == '/api/page-recordings':
                return self._send(201, app.record_page(self._json(raw)))
            if method == 'POST' and path == '/api/leads':
                return self._send(201, app.create_lead(self._json(raw)))
            match = re.fullmatch(r'/api/jobs/([a-f0-9]{32})(/render|/video)?', path)
            if not match:
                raise Refused(404, 'Not found.')
            job_id, action = match.groups()
            if method == 'POST' and action == '/render':
                return self._send(202, app.start_render(job_id, self._json(raw)))
            if method == 'GET' and not action:
                return self._send(200, app.state(job_id))
            if method == 'GET' and action == '/video':
                if app.state(job_id)['status'] != 'complete':
                    raise Refused(409, 'The video is not ready.')
                return self._send(200, (app.jobs_dir / job_id / 'final.mp4').read_bytes(), 'video/mp4')
            raise Refused(404, 'Not found.')

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def page_capture(url, out) -> str:
    try:
        done = subprocess.run([config.node22(), str(config.ROOT / 'capture/linkedin.mjs'), 'capture',
                               str(config.LINKEDIN), url, str(out)], capture_output=True, text=True, timeout=CAPTURE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError('The page capture took too long. Try again.') from None
    if done.returncode:
        lines = done.stderr.strip().splitlines()
        raise RuntimeError(lines[-1][:300] if lines else 'The page capture failed.')
    return json.loads(done.stdout.strip().splitlines()[-1])['url']


def main():
    app = App(template_dir=config.TEMPLATE, jobs_dir=config.JOBS, loom_dir=config.LOOM_CONFIG,
              worker_token=config.token('worker.token'),
              download=providers.download, lipsync=providers.fal_lipsync(lambda: config.read_secret('fal.key')),
              capture=page_capture)
    make_server(app).serve_forever()


if __name__ == '__main__':
    main()
