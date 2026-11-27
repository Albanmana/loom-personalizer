"""HeyGen (voice) and Fal (lip sync). Error messages never include keys or signed URLs."""
import json
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from app import media

HEYGEN_API = 'https://api.heygen.com/v3/models/audio'
HEYGEN_HOSTS = ('heygen.com', 'heygen.ai', 'amazonaws.com', 'googleapis.com', 'cloudfront.net')
FAL_HOSTS = ('fal.media', 'storage.googleapis.com')
FAL_MODEL = 'fal-ai/sync-lipsync/v2/pro'


def check_url(url: str, hosts):
    p = urlsplit(url)
    host = p.hostname or ''
    if (p.scheme != 'https' or p.username or p.password or p.port not in (None, 443)
            or not any(host == h or host.endswith('.' + h) for h in hosts)):
        raise ValueError(f'Refused to download from an unexpected address ({host or "none"}).')


def download(url: str, out: Path, hosts=HEYGEN_HOSTS, limit=200 * 1024 * 1024):
    check_url(url, hosts)

    class Checked(HTTPRedirectHandler):
        def redirect_request(self, request, handle, code, message, headers, new_url):
            check_url(new_url, hosts)
            return super().redirect_request(request, handle, code, message, headers, new_url)

    size = 0
    with build_opener(Checked()).open(Request(url), timeout=60) as source, Path(out).open('wb') as sink:
        while block := source.read(256 * 1024):
            size += len(block)
            if size > limit:
                raise ValueError('The downloaded file is too large.')
            sink.write(block)


def _heygen(path: str, key: str, body=None) -> dict:
    if not key:
        raise RuntimeError('The HeyGen key is missing. Run: studio.py key heygen')
    request = Request(HEYGEN_API + path, data=None if body is None else json.dumps(body).encode(),
                      headers={'X-Api-Key': key, 'Content-Type': 'application/json',
                               'User-Agent': 'loom-personalizer'})
    if body is not None:
        request.add_header('Idempotency-Key', str(uuid.uuid4()))
    try:
        with urlopen(request, timeout=180) as response:
            return json.load(response)['data']
    except HTTPError as error:
        hint = {401: 'check the API key', 402: 'check your HeyGen API credit',
                403: 'your plan does not allow this', 404: 'check the voice ID'}.get(error.code, 'try again later')
        raise RuntimeError(f'HeyGen refused the request (HTTP {error.code}): {hint}.') from None


def heygen_voices(key: str) -> list[dict]:
    return _heygen('/voices?limit=50', key)


def heygen_speech(text: str, voice_id: str, key: str, out: Path, language='en', speed=1):
    data = _heygen('/tts', key, {'voice_id': voice_id, 'text': text, 'language': language, 'speed': speed,
                                 'model': 'heygen-voice-1'})
    download(data['audio_url'], out)


def fal_lipsync(key):
    """Return lipsync(video, audio, out, state_file). A saved request is resumed, not paid twice.

    `key` is the key itself, or a function reading it when the lip sync starts."""
    def lipsync(video: Path, audio: Path, out: Path, state: Path):
        inputs = [media.file_hash(video), media.file_hash(audio)]
        saved = json.loads(state.read_text()) if state.is_file() else {}
        if saved.get('inputs') == inputs and out.is_file() and saved.get('output') == media.file_hash(out):
            return  # Already paid and downloaded. Fal deletes its copy after an hour.
        secret = key() if callable(key) else key
        if not secret:
            raise RuntimeError('The Fal key is missing. Run: studio.py key fal')
        step = 'loading the Fal client'
        try:
            from fal_client.client import Completed, StorageSettings, SyncClient
            client = SyncClient(key=secret, default_timeout=120)
            request_id = saved.get('requestId') if saved.get('inputs') == inputs else None
            if request_id is None:
                keep = StorageSettings(expires_in=3600)
                step = 'uploading the face footage'
                video_url = client.upload_file(video, lifecycle=keep, fallback_repository=[])
                step = 'uploading the voice'
                audio_url = client.upload_file(audio, lifecycle=keep, fallback_repository=[])
                step = 'sending the lip-sync request'
                request_id = client.submit(
                    FAL_MODEL, {'video_url': video_url, 'audio_url': audio_url, 'sync_mode': 'cut_off'},
                    headers={'X-Fal-Object-Lifecycle-Preference': '{"expiration_duration_seconds":3600}'},
                    start_timeout=600).request_id
                state.write_text(json.dumps({'inputs': inputs, 'requestId': request_id}))
            step = 'waiting for the lip sync'
            deadline = time.monotonic() + 900
            while not isinstance(client.status(FAL_MODEL, request_id), Completed):
                if time.monotonic() > deadline:
                    raise RuntimeError('Fal is still working after 15 minutes. Do not submit the lead again, which would pay twice: ask Claude Code to resume this job.')
                time.sleep(8)
            step = 'reading the result'
            try:
                result_url = client.result(FAL_MODEL, request_id)['video']['url']
            except Exception:
                state.unlink(missing_ok=True)  # Fal finished without a video: the next run must send it again.
                raise
            step = 'downloading the result'
            download(result_url, out, FAL_HOSTS)
        except (RuntimeError, ValueError):
            raise
        except Exception as error:  # SDK errors can embed signed URLs, so only the type is shown.
            response = getattr(error, 'response', None)
            code = getattr(response, 'status_code', getattr(error, 'status_code', None))
            raise RuntimeError(f'Fal failed while {step} ({type(error).__name__}'
                               + (f', HTTP {code}' if code else '') + ').') from None
        expected = media.streams(video)['video'].get('nb_frames')
        got = media.streams(out)['video'].get('nb_frames')
        if expected and got and abs(int(expected) - int(got)) > 2:
            raise RuntimeError(f'Fal returned {got} frames instead of {expected}.')
        state.write_text(json.dumps({'inputs': inputs, 'requestId': request_id, 'output': media.file_hash(out)}))
    return lipsync
