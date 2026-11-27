"""All FFmpeg work. No third-party Python libraries."""
import hashlib
import json
import math
import subprocess
from array import array
from pathlib import Path

FPS = 30
RATE = 48000
FACE = 720  # Square face clip sent to lip sync.
VIDEO = ('-c:v', 'libx264', '-preset', 'veryfast', '-crf', '17', '-bf', '0', '-profile:v', 'high',
         '-pix_fmt', 'yuv420p', '-g', '90', '-video_track_timescale', '15360')


def run(args, stage, timeout=900):
    try:
        return subprocess.run([str(a) for a in args], capture_output=True, check=True, timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError(f'{stage}: {args[0]} is not installed.') from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f'{stage} took too long.') from None
    except subprocess.CalledProcessError as error:
        lines = error.stderr.decode(errors='replace').strip().splitlines()
        raise RuntimeError(f'{stage} failed: {lines[-1][:200] if lines else "no details"}') from None


def ffmpeg(stage, *args):
    return run(['ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-y', *args], stage)


def streams(path) -> dict:
    result = run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', path], f'Reading {Path(path).name}')
    return {s['codec_type']: s for s in json.loads(result.stdout)['streams']}


def duration(path) -> float:
    result = run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                 f'Measuring {Path(path).name}')
    return float(result.stdout)


def file_hash(path) -> str:
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


# --- Sync of the two recordings ---------------------------------------------

def _envelope(path) -> list[float]:
    """Loudness every 10 ms, centred on zero."""
    raw = ffmpeg(f'Reading the sound of {Path(path).name}', '-i', path, '-map', '0:a:0', '-ac', '1',
                 '-ar', '8000', '-f', 's16le', '-').stdout
    samples = array('h', raw)
    values = [math.log1p(sum(map(abs, samples[i:i + 80])) / 80) for i in range(0, len(samples) - 79, 80)]
    if len(values) < 300:
        raise ValueError(f'{Path(path).name} has less than 3 seconds of sound.')
    mean = sum(values) / len(values)
    return [v - mean for v in values]


def find_offset(camera, screen, max_seconds=30) -> tuple[float, float]:
    """Return (offset, confidence): camera time t is heard at screen time t + offset."""
    a, b = _envelope(camera), _envelope(screen)

    # A short overlap can match by chance, so require half of the shorter recording.
    minimum = max(300, min(len(a), len(b), 6000) // 2)

    def score(lag):
        x, y = a[max(0, -lag):], b[max(0, lag):]
        n = min(len(x), len(y), 6000)  # One minute is plenty and keeps this fast.
        if n < minimum:
            return 0.0
        x, y = x[:n], y[:n]
        energy = math.sqrt(math.sumprod(x, x) * math.sumprod(y, y)) if hasattr(math, 'sumprod') else \
            math.sqrt(sum(v * v for v in x) * sum(v * v for v in y))
        dot = math.sumprod(x, y) if hasattr(math, 'sumprod') else sum(i * j for i, j in zip(x, y))
        return dot / energy if energy else 0.0

    scores = {lag: score(lag) for lag in range(-max_seconds * 100, max_seconds * 100 + 1)}
    best = max(scores, key=scores.get)
    runner_up = max((v for lag, v in scores.items() if abs(lag - best) > 25), default=0.0)
    confidence = scores[best] / max(runner_up, 0.05) if scores[best] > 0.25 else 1.0
    return best / 100, confidence


# --- Retiming ----------------------------------------------------------------

def plan_segments(start, end, frames, anchors):
    """Map source seconds [start, end] onto `frames` output frames.

    Each anchor [camera_seconds, voice_seconds] pins a source moment to a moment of the new voice.
    Without anchors the whole part gets one speed.
    """
    points = [(float(start), 0)]
    for source, spoken in anchors or []:
        frame = round(spoken * FPS)
        if not (points[-1][0] < source < end and points[-1][1] < frame < frames):
            raise ValueError(f'Anchor [{source}, {spoken}] is outside the demo or out of order.')
        points.append((float(source), frame))
    points.append((float(end), frames))
    return [(a, c, d - b) for (a, b), (c, d) in zip(points, points[1:])]


def _retime(stream, segments, shape, label):
    """Filter chain cutting each segment, stretching it to its frame count and joining them."""
    count = len(segments)
    chains = [f'[{stream}]fps={FPS},{shape},setsar=1,format=yuv420p,split={count}'
              + ''.join(f'[{label}i{n}]' for n in range(count))]
    for n, (a, b, frames) in enumerate(segments):
        chains.append(f'[{label}i{n}]trim=start={a:.4f}:end={b:.4f},setpts=(PTS-STARTPTS)*'
                      f'{frames / FPS / (b - a):.10f},fps={FPS},tpad=stop_mode=clone:stop={FPS},'
                      f'trim=end_frame={frames},settb=1/{FPS},setpts=N[{label}o{n}]')
    chains.append(''.join(f'[{label}o{n}]' for n in range(count))
                  + f'concat=n={count}:v=1:a=0,settb=1/{FPS},setpts=N[{label}]')
    return ';'.join(chains)


def face_clip(camera, crop, segments, out):
    w, h, x, y = crop
    graph = _retime('0:v', segments, f'crop={w}:{h}:{x}:{y},scale={FACE}:{FACE}:flags=lanczos', 'face')
    ffmpeg('Preparing the face footage', '-i', camera, '-filter_complex', graph, '-map', '[face]', '-an',
           *VIDEO, '-movflags', '+faststart', out)


def screen_clip(screen, crop, segments, out):
    w, h, x, y = crop
    shape = (f'crop={w}:{h}:{x}:{y},scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,'
             'pad=1920:1080:(ow-iw)/2:(oh-ih)/2')
    ffmpeg('Preparing the demo screen', '-i', screen, '-filter_complex',
           _retime('0:v', segments, shape, 'screen'), '-map', '[screen]', '-an', *VIDEO, out)


def fit_voice(source, out, frames):
    """Mono 48 kHz WAV of exactly `frames` video frames, padded with silence if needed."""
    samples = frames * RATE // FPS
    # Resample first: apad and atrim count samples at the rate they receive.
    ffmpeg('Preparing the voice', '-i', source, '-vn', '-ac', '1', '-af',
           f'aresample={RATE},apad=whole_len={samples},atrim=end_sample={samples}', '-c:a', 'pcm_s16le', out)


def compose(base, face, ring, bubble, frames, out):
    """Put the round face bubble on the base video."""
    size, x, y = bubble['size'], bubble['x'], bubble['y']
    outer = size + 20
    centre, radius = (size - 1) / 2, size / 2 - 0.2
    graph = (
        f'[0:v]scale=1920:1080:flags=lanczos,setsar=1,fps={FPS},tpad=stop_mode=clone:stop={frames},'
        f'trim=end_frame={frames},settb=1/{FPS},setpts=N[base];'
        f'[1:v]fps={FPS},tpad=stop_mode=clone:stop={FPS},trim=end_frame={frames},scale={size}:{size}:flags=lanczos,'
        f'format=yuva444p,settb=1/{FPS},setpts=N,'
        f"geq=lum='lum(X,Y)':cb='cb(X,Y)':cr='cr(X,Y)':a='255*clip({radius}-hypot(X-{centre},Y-{centre}),0,1)'[face];"
        f'[2:v]scale={outer}:{outer}:flags=lanczos,format=rgba[ring];'
        '[ring][face]overlay=10:10:shortest=1:format=auto[bubble];'
        f'[base][bubble]overlay={x}:{y}:format=yuv420,format=yuv420p,setsar=1[out]'
    )
    ffmpeg('Composing the video', '-i', base, '-i', face, '-loop', '1', '-framerate', str(FPS), '-i', ring,
           '-filter_complex', graph, '-map', '[out]', '-an', '-frames:v', str(frames), *VIDEO, out)


def join(videos, voices, out, scratch: Path):
    """Join picture without re-encoding and encode the whole voice as one track."""
    listing = scratch / 'picture.txt'
    listing.write_text(''.join(f"file '{Path(v).resolve()}'\n" for v in videos))
    inputs = [a for v in voices for a in ('-i', v)]
    graph = ''.join(f'[{i + 1}:a]' for i in range(len(voices))) + f'concat=n={len(voices)}:v=0:a=1[voice]'
    ffmpeg('Joining the video', '-f', 'concat', '-safe', '0', '-i', listing, *inputs, '-filter_complex', graph,
           '-map', '0:v:0', '-map', '[voice]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '160k',
           '-movflags', '+faststart', out)


def fit_page(source, seconds, out):
    """Make a saved page recording last exactly as long as the intro.

    Longer recordings are sped up. Shorter ones are slowed by up to 1.2x, then hold their last picture.
    """
    count = round(seconds * FPS)
    stretch = min(1.2, seconds / duration(source))
    ffmpeg('Fitting the saved page recording', '-i', source, '-vf',
           f'setpts=(PTS-STARTPTS)*{stretch:.10f},fps={FPS},'
           'scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,'
           f'pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,tpad=stop_mode=clone:stop={count},'
           f'trim=end_frame={count}', '-an', *VIDEO, out)
