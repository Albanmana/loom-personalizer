"""Two steps: prepare the static demo once, then render one short intro per lead."""
import hashlib
import json
import math
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from app import media, template

JOIN_PAUSE = 0.35  # Silence after the intro, before the demo starts.
RING = Path(__file__).resolve().parent.parent / 'assets' / 'camera-ring.png'


def _checked(folder: Path) -> dict:
    t = template.load(folder)
    found = template.problems(t)
    if found:
        raise ValueError('The template is not ready:\n- ' + '\n- '.join(found))
    return t


def _recording(folder: Path, t: dict, key: str) -> Path:
    path = Path(folder) / t['recordings'][key]
    if not path.is_file():
        raise ValueError(f'The {key} recording is missing: {path}')
    return path


def prepare_demo(folder: Path, voice: Path, lipsync) -> dict:
    """Fit the user's screen and face to the AI demo voice, lip-sync once, keep the result."""
    folder = Path(folder)
    t = _checked(folder)
    camera, screen = _recording(folder, t, 'camera'), _recording(folder, t, 'screen')
    demo = t['demo']
    offset = t['recordings']['screenOffset']
    if demo['start'] + offset < 0 or demo['end'] + offset > media.duration(screen) + 0.05:
        raise ValueError('The screen recording does not cover the whole demo. Check screenOffset.')
    if demo['end'] > media.duration(camera) + 0.05:
        raise ValueError('demo.end is after the end of the camera recording.')
    frames = math.ceil(media.duration(voice) * media.FPS)
    segments = media.plan_segments(demo['start'], demo['end'], frames, demo.get('anchors'))
    (folder / 'demo.approved').unlink(missing_ok=True)
    work = folder / 'demo-work'
    work.mkdir(exist_ok=True)
    fitted = work / 'voice.wav'
    media.fit_voice(voice, fitted, frames)
    # Anchors only retime the screen. The face keeps one speed, so adjusting clicks does not pay for lip sync again.
    media.face_clip(camera, t['camera']['crop'], [(demo['start'], demo['end'], frames)], work / 'face.mp4')
    media.screen_clip(screen, t['screen']['crop'], [(a + offset, b + offset, n) for a, b, n in segments],
                      work / 'screen.mp4')
    lipsync(work / 'face.mp4', fitted, work / 'face-synced.mp4', work / 'lipsync.json')
    media.compose(work / 'screen.mp4', work / 'face-synced.mp4', RING, t['bubble'], frames, work / 'picture.mp4')
    shutil.copyfile(work / 'picture.mp4', folder / 'demo-picture.mp4')
    shutil.copyfile(fitted, folder / 'demo.wav')
    media.join([folder / 'demo-picture.mp4'], [folder / 'demo.wav'], folder / 'demo-preview.mp4', work)
    speed = (demo['end'] - demo['start']) / (frames / media.FPS)
    return {'frames': frames, 'seconds': frames / media.FPS, 'screenSpeed': round(speed, 3),
            'preview': str(folder / 'demo-preview.mp4')}


def _demo_fingerprint(folder: Path) -> str:
    """Everything that changes the demo. The intro wording is deliberately left out."""
    t = template.load(folder)
    relevant = {k: t.get(k) for k in ('voiceId', 'speed', 'demo', 'camera', 'screen', 'bubble', 'recordings')}
    digest = hashlib.sha256(json.dumps(relevant, sort_keys=True).encode())
    for name in ('demo-picture.mp4', 'demo.wav'):
        digest.update(media.file_hash(Path(folder) / name).encode())
    return digest.hexdigest()


def approve_demo(folder: Path):
    folder = Path(folder)
    if not (folder / 'demo-picture.mp4').is_file():
        raise ValueError('There is no demo to approve yet.')
    (folder / 'demo.approved').write_text(_demo_fingerprint(folder))


def demo_is_approved(folder: Path) -> bool:
    marker = Path(folder) / 'demo.approved'
    return marker.is_file() and marker.read_text() == _demo_fingerprint(folder)


def render_lead(folder: Path, job: Path, page_video, lipsync) -> dict:
    """job/voice.wav is the new intro voice. page_video(seconds, out) writes the lead's page clip."""
    folder, job = Path(folder), Path(job)
    t = _checked(folder)
    if not demo_is_approved(folder):
        raise ValueError('Prepare and approve the demo first (it is missing or the template changed).')
    camera = _recording(folder, t, 'camera')
    voice = job / 'voice.wav'
    frames = math.ceil((media.duration(voice) + JOIN_PAUSE) * media.FPS)
    seconds = frames / media.FPS
    with TemporaryDirectory(prefix='render-', dir=job) as temp:
        scratch = Path(temp)
        fitted = scratch / 'intro.wav'
        media.fit_voice(voice, fitted, frames)
        page = job / 'page.mp4'
        if not page.is_file() or abs(media.duration(page) - seconds) > 0.1:
            page_video(seconds, page)
        intro = t['intro']
        media.face_clip(camera, t['camera']['crop'], [(intro['start'], intro['end'], frames)], scratch / 'face.mp4')
        synced = job / 'face-synced.mp4'  # Kept with the job: a retry must not pay for it again.
        lipsync(scratch / 'face.mp4', fitted, synced, job / 'lipsync.json')
        media.compose(page, synced, RING, t['bubble'], frames, scratch / 'intro.mp4')
        media.join([scratch / 'intro.mp4', folder / 'demo-picture.mp4'], [fitted, folder / 'demo.wav'],
                   scratch / 'final.mp4', scratch)
        (scratch / 'final.mp4').replace(job / 'final.mp4')
    return {'introFrames': frames, 'cutAt': round(seconds, 3),
            'duration': round(media.duration(job / 'final.mp4'), 3)}
