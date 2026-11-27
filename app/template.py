"""The one file that describes a user's video: data/template/template.json."""
import json
import re
from pathlib import Path

VARIABLE = re.compile(r'\{([^{}\n]+)\}')


def load(folder: Path) -> dict:
    path = Path(folder) / 'template.json'
    if not path.is_file():
        raise ValueError(f'No template yet: {path} is missing.')
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise ValueError(f'template.json is not valid JSON (line {error.lineno}).') from None


def variables(t: dict) -> list[str]:
    return list(dict.fromkeys(name.strip() for name in VARIABLE.findall(t.get('intro', {}).get('text', ''))))


def fill(text: str, values: dict) -> str:
    """Put the lead's values in place of the {names}. Capitals and outer spaces in a name do not matter."""
    if not text.strip():
        raise ValueError('The intro script is empty.')
    if re.search(r'[{}]', VARIABLE.sub('', text)):
        raise ValueError('The intro script has a brace without its pair. Write each variable as {Name}.')
    cleaned = {str(k).strip().casefold(): ' '.join(str(v).split()) for k, v in (values or {}).items()}
    names = dict.fromkeys(name.strip() for name in VARIABLE.findall(text))
    missing = [name for name in names if not cleaned.get(name.casefold())]
    if missing:
        raise ValueError('Missing values for: ' + ', '.join(missing))
    return VARIABLE.sub(lambda m: cleaned[m[1].strip().casefold()], text)


def render_intro(t: dict, values: dict) -> str:
    return fill(t['intro']['text'], values)


def problems(t: dict) -> list[str]:
    found = []
    if not t.get('voiceId'):
        found.append('voiceId is empty: pick your HeyGen voice.')
    for part in ('intro', 'demo'):
        section = t.get(part) or {}
        if not str(section.get('text', '')).strip():
            found.append(f'{part}.text is empty.')
        start, end = section.get('start'), section.get('end')
        if not (isinstance(start, (int, float)) and isinstance(end, (int, float)) and 0 <= start < end):
            found.append(f'{part}.start and {part}.end must be camera seconds with start < end.')
    if VARIABLE.search((t.get('demo') or {}).get('text', '')):
        found.append('demo.text cannot contain variables: the demo is the same for everyone.')
    recordings = t.get('recordings') or {}
    for key in ('camera', 'screen'):
        if not recordings.get(key):
            found.append(f'recordings.{key} is empty.')
        crop = (t.get(key) or {}).get('crop')
        if not (isinstance(crop, list) and len(crop) == 4 and all(isinstance(v, int) and v >= 0 for v in crop)
                and crop[0] and crop[1]):
            found.append(f'{key}.crop must be [width, height, x, y] in pixels.')
    if not isinstance(recordings.get('screenOffset'), (int, float)):
        found.append('recordings.screenOffset is missing: run `studio.py sync`.')
    bubble = t.get('bubble') or {}
    if not all(isinstance(bubble.get(k), int) for k in ('x', 'y', 'size')):
        found.append('bubble needs whole numbers x, y and size.')
    return found
