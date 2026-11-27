"""Small synthetic media, so the real FFmpeg graph runs without paid services."""
import json
import random
import subprocess
import wave
from array import array
from pathlib import Path


def ff(*args):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *map(str, args)], check=True)


def speech_like_wav(path, seconds, seed=1, delay=0.0, rate=8000):
    """Noise bursts of random length and loudness: a distinctive envelope, like speech."""
    rng = random.Random(seed)
    samples = array('h', [0]) * int(delay * rate)
    total = int((seconds + delay) * rate)
    while len(samples) < total:
        level = rng.choice([0, 0, 1500, 4000, 9000])
        samples.extend(rng.randint(-level, level) if level else 0 for _ in range(int(rng.uniform(.08, .4) * rate)))
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(rate)
        out.writeframes(samples[:total].tobytes())


def video(path, seconds, size='640x360'):
    ff('-f', 'lavfi', '-i', f'testsrc2=size={size}:rate=30', '-t', seconds, '-c:v', 'libx264',
       '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', path)


def template_folder(folder: Path) -> Path:
    """A template with a 10 s camera take and a screen take that started 1.5 s earlier."""
    (folder / 'recordings').mkdir(parents=True)
    speech_like_wav(folder / 'cam.wav', 10)
    speech_like_wav(folder / 'screen.wav', 10, delay=1.5)
    video(folder / 'cam-picture.mp4', 10)
    video(folder / 'screen-picture.mp4', 11.5)
    for name in ('cam', 'screen'):
        ff('-i', folder / f'{name}-picture.mp4', '-i', folder / f'{name}.wav', '-c:v', 'copy', '-c:a', 'aac',
           folder / 'recordings' / f'{"camera" if name == "cam" else "screen"}.mp4')
    (folder / 'template.json').write_text(json.dumps({
        'voiceId': 'voice-1', 'language': 'en',
        'recordings': {'camera': 'recordings/camera.mp4', 'screen': 'recordings/screen.mp4', 'screenOffset': 1.5},
        'intro': {'start': 0.5, 'end': 3.5, 'text': 'Hey {firstName}, I saw you work at {company}.'},
        'demo': {'start': 3.5, 'end': 9.5, 'text': 'This is the tool.', 'anchors': []},
        'camera': {'crop': [360, 360, 140, 0]}, 'screen': {'crop': [640, 360, 0, 0]},
        'bubble': {'x': 34, 'y': 795, 'size': 232},
    }))
    return folder
