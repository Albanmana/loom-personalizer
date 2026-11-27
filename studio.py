#!/usr/bin/env python3
"""Setup and preparation commands, meant to be run by Claude Code (see CLAUDE.md)."""
import argparse
import getpass
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from app import config, media, pipeline, providers, template

ROOT = config.ROOT
CRED_WORKER, CRED_HEYGEN, CRED_UPLOADER = 'lpWorker000000001', 'lpHeygen000000001', 'lpUpload000000001'
PROFILE_FIELD = 'LinkedIn profile URL'
FAL_PER_MINUTE = 5.0  # Fal's listed price for Sync Lipsync 2 Pro. Check it before relying on estimates.
MODEL = config.DATA / 'models' / 'ggml-small.en.bin'


def lipsync_cost(seconds: float) -> float:
    return seconds / 60 * FAL_PER_MINUTE


def installed_python(root: Path, prefix: str):
    """The package's own Python, if it exists and we are not already running in it."""
    python = root / '.venv/bin/python'
    inside = Path(prefix).resolve() == (root / '.venv').resolve()
    return str(python) if python.is_file() and not inside else None


def venv_python() -> str:
    candidate = ROOT / '.venv/bin/python'
    return str(candidate) if candidate.is_file() else sys.executable


node22 = config.node22


def n8n_command() -> list[str]:
    override = os.environ.get('LP_N8N_BIN')
    if override:
        return [override]
    return [node22(), str(config.DATA / 'tools/node_modules/n8n/bin/n8n')]


def n8n_installed() -> bool:
    """n8n can be on disk yet unable to start: its sqlite3 module needs a build step npm may have skipped."""
    if os.environ.get('LP_N8N_BIN'):
        return True
    if not Path(n8n_command()[-1]).is_file():
        return False
    probe = subprocess.run([node22(), '-e', "require('sqlite3')"], cwd=config.DATA / 'tools', capture_output=True)
    return probe.returncode == 0


def service_env() -> dict:
    return {**os.environ,
            'LOOM_CONFIG_DIR': str(config.LOOM_CONFIG),
            'N8N_USER_FOLDER': str(config.DATA / 'n8n'), 'N8N_HOST': '127.0.0.1',
            'N8N_LISTEN_ADDRESS': '127.0.0.1', 'N8N_PORT': str(config.N8N_PORT),
            'N8N_RUNNERS_BROKER_PORT': str(config.N8N_PORT + 1),
            'N8N_EDITOR_BASE_URL': f'http://127.0.0.1:{config.N8N_PORT}/',
            'WEBHOOK_URL': f'http://127.0.0.1:{config.N8N_PORT}/',
            'N8N_SECURE_COOKIE': 'false', 'N8N_DIAGNOSTICS_ENABLED': 'false',
            'N8N_VERSION_NOTIFICATIONS_ENABLED': 'false', 'N8N_PERSONALIZATION_ENABLED': 'false'}


def n8n_credentials(heygen: str, worker: str, uploader: str) -> list[dict]:
    def header(ident, name, field, value):
        return {'id': ident, 'name': name, 'type': 'httpHeaderAuth', 'data': {'name': field, 'value': value}}
    return [header(CRED_WORKER, 'Loom Personalizer · local worker', 'Authorization', f'Bearer {worker}'),
            header(CRED_HEYGEN, 'Loom Personalizer · HeyGen', 'X-Api-Key', heygen),
            header(CRED_UPLOADER, 'Loom Personalizer · Loom uploader', 'Authorization', f'Bearer {uploader}')]


def field_label(variable: str) -> str:
    """firstName or first_name -> "First name". Words, capitalised names and acronyms are kept as written."""
    if ' ' in variable or (variable[:1].isupper() and '_' not in variable):
        return variable
    words = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', ' ', variable).replace('_', ' ').lower()
    return words[:1].upper() + words[1:]


def lead_workflow(base: dict, t: dict) -> dict:
    """The packaged workflow with this user's form: one field per script variable, and the script in sight."""
    workflow = json.loads(json.dumps(base))
    for node in workflow['nodes']:
        if 'webhookId' in node:  # Whoever knows the form's address can submit it: each install gets its own.
            node['webhookId'] = str(uuid.uuid4())
    nodes = {n['name']: n['parameters'] for n in workflow['nodes']}
    labels = {}
    for name in template.variables(t):
        label = field_label(name)
        if label.casefold() in (other.casefold() for other in (PROFILE_FIELD, *labels.values())):
            raise ValueError(f'Two form fields would be called "{label}": rename a variable in intro.text.')
        labels[name] = label
    intro = template.VARIABLE.sub(lambda m: '{' + labels[m[1].strip()] + '}', t['intro']['text'])
    nodes['Intro script']['assignments']['assignments'][0]['value'] = intro
    nodes['New lead']['formFields']['values'][:0] = [{'fieldLabel': label, 'requiredField': True}
                                                    for label in labels.values()]
    nodes['Your script']['content'] = (
        '## Your script\n**To change the intro**, open **Intro script**, edit the text, then **Save** and **Publish**. '
        'A name in braces takes the value of the form field with that name: to add one, add the field in **New lead**.\n\n'
        f"**Demo** (already recorded, same for everyone; editing this note does not change it)\n{t['demo']['text']}")
    return workflow


def uploader_token() -> str:
    path = config.private_dir(config.LOOM_CONFIG) / 'api.token'
    if not path.is_file():
        import secrets
        handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(handle, 'w') as out:
            out.write(secrets.token_hex(32) + '\n')
    return path.read_text().strip()


# --- commands ------------------------------------------------------------------

def cmd_init(_):
    for folder in (config.DATA, config.SECRETS, config.TEMPLATE / 'recordings', config.JOBS, config.LOOM_CONFIG,
                   config.DATA / 'page-recordings'):
        config.private_dir(folder)
    config.token('worker.token')
    uploader_token()
    target = config.TEMPLATE / 'template.json'
    if not target.exists():
        shutil.copyfile(ROOT / 'assets/template.example.json', target)
    print(f'Ready. Private data lives in {config.DATA}')


def ask_secret(prompt: str, from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline()
    if sys.stdin.isatty():
        return getpass.getpass(f'{prompt} (hidden): ')
    # Run by an agent: ask in a native macOS dialog so the secret never enters the chat.
    script = (f'text returned of (display dialog "Paste your {prompt}. It is saved only on this Mac." '
              'default answer "" with hidden answer with title "Loom Personalizer")')
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ''


def cmd_loom(_):
    print('A browser window opens on loom.com. Sign in there; it closes by itself once Loom accepts the sign-in.',
          flush=True)
    sys.exit(subprocess.run([node22(), str(ROOT / 'uploader/loom-login.mjs'),
                             str(config.private_dir(config.LOOM_CONFIG) / 'session.cookie')]).returncode)


def cmd_key(args):
    label = {'heygen': 'HeyGen', 'fal': 'Fal'}[args.name]
    value = ask_secret(f'{label} API key', args.stdin)
    if len(value.strip()) < 10 or any(c.isspace() for c in value.strip()):
        sys.exit('No key saved.')
    config.write_secret(f'{args.name}.key', value)
    print(f'{label} key saved (not shown).')


def cmd_voices(_):
    voices = providers.heygen_voices(config.read_secret('heygen.key'))
    if not voices:
        print('No cloned voice found. Create one in HeyGen, then run this again.')
    for v in voices:
        print(f"{v['voice_id']}  {v.get('status', '?'):8}  {v.get('mode', '')}  {v.get('name', '')}")


def cmd_sync(args):
    t = template.load(config.TEMPLATE)
    paths = [config.TEMPLATE / t['recordings'][k] for k in ('camera', 'screen')]
    offset, confidence = media.find_offset(*paths)
    print(f'The screen recording is {offset:+.2f} s from the camera recording (confidence {confidence:.1f}).')
    if confidence < 2 and not args.force:
        sys.exit('Confidence is too low, so nothing was saved. The screen recording must hear the camera '
                 'video through the speakers, with its microphone on.')
    t['recordings']['screenOffset'] = offset
    (config.TEMPLATE / 'template.json').write_text(json.dumps(t, indent=2) + '\n')
    print('Saved in template.json.')


def cmd_transcribe(args):
    t = template.load(config.TEMPLATE)
    model = Path(args.model or MODEL)
    if not model.is_file():
        sys.exit(f'Speech model not found at {model}. Run ./setup.sh first.')
    with TemporaryDirectory() as folder:
        wav = Path(folder) / 'take.wav'
        media.ffmpeg('Extracting the voice', '-i', config.TEMPLATE / t['recordings']['camera'], '-vn',
                     '-ac', '1', '-ar', '16000', wav)
        out = Path(folder) / 'transcript'
        media.run(['whisper-cli', '-m', model, '-f', wav, '-l', args.language, '-ml', '1', '-sow',
                   '-oj', '-of', out, '-np'], 'Transcribing')
        words = json.loads(out.with_suffix('.json').read_text())['transcription']
    result = [{'start': w['offsets']['from'] / 1000, 'end': w['offsets']['to'] / 1000, 'word': w['text'].strip()}
              for w in words if w['text'].strip()]
    target = config.TEMPLATE / 'transcript.json'
    target.write_text(json.dumps(result, indent=1) + '\n')
    line, start = [], None
    for i, w in enumerate(result):
        start = w['start'] if start is None else start
        line.append(w['word'])
        pause = i + 1 == len(result) or result[i + 1]['start'] - w['end'] > 0.45
        if pause or w['word'].endswith(('.', '?', '!')):
            print(f'[{start:7.2f} - {w["end"]:7.2f}] ' + ' '.join(line))
            line, start = [], None
    print(f'Word times saved in {target}')


def cmd_check(_):
    t = template.load(config.TEMPLATE)
    found = template.problems(t)
    for key in ('camera', 'screen'):
        if t.get('recordings', {}).get(key) and not (config.TEMPLATE / t['recordings'][key]).is_file():
            found.append(f"recordings.{key}: file not found ({t['recordings'][key]}).")
    if found:
        sys.exit('Not ready:\n- ' + '\n- '.join(found))
    print('Template is complete. Variables: ' + (', '.join(template.variables(t)) or 'none'))
    print('Demo approved.' if pipeline.demo_is_approved(config.TEMPLATE) else 'Demo not approved yet.')


def cmd_frame(args):
    """Save one frame of a recording, so the crop can be chosen by looking at it."""
    t = template.load(config.TEMPLATE)
    out = config.TEMPLATE / f'{args.which}-frame.jpg'
    media.ffmpeg('Saving a frame', '-ss', str(args.at), '-i', config.TEMPLATE / t['recordings'][args.which],
                 '-frames:v', '1', '-q:v', '3', out)
    stream = media.streams(out)['video']
    print(f"{out}  ({stream['width']}x{stream['height']} pixels)")


def cmd_demo(args):
    t = template.load(config.TEMPLATE)
    if args.action == 'approve':
        pipeline.approve_demo(config.TEMPLATE)
        print('Demo approved. n8n can now make videos.')
        return
    found = template.problems(t)
    if found:
        sys.exit('Not ready:\n- ' + '\n- '.join(found))
    seconds = t['demo']['end'] - t['demo']['start']
    print(f'About {seconds:.0f} s of demo: about ${lipsync_cost(seconds):.2f} of Fal lip sync (listed price), '
          'plus HeyGen voice. The AI voice length can differ from your recording.')
    if not args.yes:
        print('Nothing was spent. Run again with --yes after the user agrees.')
        return
    voice = config.TEMPLATE / 'demo-voice.wav'
    key_file = config.TEMPLATE / 'demo-voice.key'
    wanted = json.dumps([t['demo']['text'], t['voiceId'], t.get('language', 'en'), t.get('speed', 1)])
    if not voice.is_file() or not key_file.is_file() or key_file.read_text() != wanted:
        providers.heygen_speech(t['demo']['text'], t['voiceId'], config.read_secret('heygen.key'), voice,
                                t.get('language', 'en'), t.get('speed', 1))
        key_file.write_text(wanted)
    result = pipeline.prepare_demo(config.TEMPLATE, voice, providers.fal_lipsync(config.read_secret('fal.key')))
    print(json.dumps(result, indent=2))
    print('Watch the preview. If it is right: python3 studio.py demo approve')


def cmd_linkedin(_):
    print('A browser window opens on linkedin.com. Sign in there; it closes by itself when your feed appears.')
    done = subprocess.run([node22(), str(ROOT / 'capture/linkedin.mjs'), 'login',
                           str(config.private_dir(config.LINKEDIN))])
    if done.returncode:
        sys.exit(done.returncode)


def cmd_n8n_setup(args):
    heygen = config.read_secret('heygen.key')
    if not heygen:
        sys.exit('Save the HeyGen key first: python3 studio.py key heygen')
    # Once installed, the script and form are the user's: they edit them in n8n.
    installed = config.DATA / 'n8n-workflow.installed'
    workflow = None
    if args.replace or not installed.is_file() or not installed.read_text():
        t = template.load(config.TEMPLATE)
        found = template.problems(t)
        if found:
            sys.exit('Finish the template first, the form is made from it:\n- ' + '\n- '.join(found))
        workflow = lead_workflow(json.loads((ROOT / 'n8n/workflow.json').read_text()), t)
    env = service_env()
    with TemporaryDirectory(dir=config.private_dir(config.DATA)) as folder:  # Removed right after import.
        secret_file = Path(folder) / 'credentials.json'
        handle = os.open(secret_file, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(handle, 'w') as out:
            json.dump(n8n_credentials(heygen, config.token('worker.token'), uploader_token()), out)
        subprocess.run([*n8n_command(), 'import:credentials', f'--input={secret_file}'], env=env, check=True)
        if workflow:
            workflow_file = Path(folder) / 'workflow.json'
            workflow_file.write_text(json.dumps(workflow))
            subprocess.run([*n8n_command(), 'import:workflow', f'--input={workflow_file}'], env=env, check=True)
    if workflow:
        subprocess.run([*n8n_command(), 'publish:workflow', '--id=loomPersonalizer1'], env=env, check=True)
        installed.write_text(next(n['webhookId'] for n in workflow['nodes'] if n['name'] == 'New lead'))
        print('n8n workflow and credentials imported and published.')
    else:
        print('Credentials updated. Your n8n workflow was left as it is, with your edits.\n'
              'To rebuild it from the template, which erases those edits: studio.py n8n-setup --replace')
    form = installed.read_text()
    print('Restart services: studio.py stop, then start. Then:\n'
          f'  Lead form (daily use): http://127.0.0.1:{config.N8N_PORT}/form/{form}\n'
          f'  Workflow (edit the intro): http://127.0.0.1:{config.N8N_PORT}/workflow/loomPersonalizer1')


def _services():
    return {
        'worker': [venv_python(), '-m', 'app.server'],
        'uploader': [node22(), str(ROOT / 'uploader/loom-upload.mjs'), 'serve', '--port', str(config.UPLOADER_PORT)],
        'n8n': [*n8n_command(), 'start'],
    }


def _running(pid_file: Path):
    """Return the process group only if it is still the process this package started."""
    if not pid_file.is_file():
        return None
    saved = json.loads(pid_file.read_text())
    probe = subprocess.run(['ps', '-o', 'lstart=', '-p', str(saved['pid'])], capture_output=True, text=True)
    return saved['pid'] if probe.stdout.strip() == saved['started'] else None


def cmd_start(_):
    logs, run = config.private_dir(config.DATA / 'logs'), config.private_dir(config.DATA / 'run')
    for name, command in _services().items():
        if _running(run / f'{name}.json'):
            print(f'{name} is already running.')
            continue
        with (logs / f'{name}.log').open('ab') as log:
            process = subprocess.Popen(command, cwd=ROOT, env=service_env(), stdout=log, stderr=log,
                                       stdin=subprocess.DEVNULL, start_new_session=True)
        started = subprocess.run(['ps', '-o', 'lstart=', '-p', str(process.pid)], capture_output=True, text=True)
        (run / f'{name}.json').write_text(json.dumps({'pid': process.pid, 'started': started.stdout.strip()}))
        print(f'{name} started (log: data/logs/{name}.log)')
    print(f'n8n: http://127.0.0.1:{config.N8N_PORT}/  (it can take a minute the first time)')


def _group_exited(group: int, seconds: float) -> bool:
    """A service that is still shutting down keeps its port, and would answer in place of the next one."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.killpg(group, 0)
        except (ProcessLookupError, PermissionError):  # macOS says "not permitted" when only a zombie is left.
            return True
        time.sleep(0.1)
    return False


def cmd_stop(_):
    for name in _services():
        pid_file = config.DATA / 'run' / f'{name}.json'
        pid = _running(pid_file)
        if pid:
            os.killpg(pid, signal.SIGTERM)
            if not _group_exited(pid, 30):
                os.killpg(pid, signal.SIGKILL)
                _group_exited(pid, 5)
            print(f'{name} stopped.')
        pid_file.unlink(missing_ok=True)


def _http_ok(url):
    try:
        with urlopen(url, timeout=3) as response:
            return json.load(response) if 'json' in response.headers.get('content-type', '') else True
    except Exception:
        return None


def cmd_doctor(_):
    rows = []

    def row(name, ok, fix=''):
        rows.append(ok)
        print(f"{'OK     ' if ok else 'MISSING'} {name}" + ('' if ok else f'  ->  {fix}'))

    for tool in ('ffmpeg', 'ffprobe', 'whisper-cli'):
        row(tool, bool(shutil.which(tool)), './setup.sh')
    version = subprocess.run([node22(), '--version'], capture_output=True, text=True).stdout.strip()
    row(f'Node 22 ({version or "not found"})', version.startswith('v22.'), './setup.sh')
    row('n8n', n8n_installed(), './setup.sh')
    has_fal = subprocess.run([venv_python(), '-c', 'import fal_client'], capture_output=True).returncode == 0
    row('fal-client (Python)', has_fal, './setup.sh')
    row('speech model', MODEL.is_file(), './setup.sh')
    row('HeyGen key', bool(config.read_secret('heygen.key')), 'python3 studio.py key heygen')
    row('Fal key', bool(config.read_secret('fal.key')), 'python3 studio.py key fal')
    health = _http_ok(f'http://127.0.0.1:{config.WORKER_PORT}/health')
    row('local worker running', bool(health), 'python3 studio.py start')
    row('uploader running', bool(_http_ok(f'http://127.0.0.1:{config.UPLOADER_PORT}/health')), 'python3 studio.py start')
    row('n8n running', bool(_http_ok(f'http://127.0.0.1:{config.N8N_PORT}/healthz')), 'python3 studio.py start')
    row('Loom session', (config.LOOM_CONFIG / 'session.cookie').is_file(), 'python3 studio.py loom')
    row('LinkedIn sign-in', (config.LINKEDIN / 'signed-in').is_file(), 'python3 studio.py linkedin')
    try:
        ready = not template.problems(template.load(config.TEMPLATE))
    except ValueError:
        ready = False
    row('template complete', ready, 'python3 studio.py check')
    row('demo approved', ready and pipeline.demo_is_approved(config.TEMPLATE), 'python3 studio.py demo prepare')
    print(f'{sum(rows)}/{len(rows)} ready.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name, handler in [('init', cmd_init), ('voices', cmd_voices), ('check', cmd_check),
                          ('start', cmd_start), ('stop', cmd_stop),
                          ('doctor', cmd_doctor)]:
        sub.add_parser(name).set_defaults(run=handler)
    sub.add_parser('loom').set_defaults(run=cmd_loom)
    sub.add_parser('linkedin').set_defaults(run=cmd_linkedin)
    n8n = sub.add_parser('n8n-setup'); n8n.add_argument('--replace', action='store_true')
    n8n.set_defaults(run=cmd_n8n_setup)
    key = sub.add_parser('key'); key.add_argument('name', choices=['heygen', 'fal'])
    key.add_argument('--stdin', action='store_true'); key.set_defaults(run=cmd_key)
    sync = sub.add_parser('sync'); sync.add_argument('--force', action='store_true'); sync.set_defaults(run=cmd_sync)
    words = sub.add_parser('transcribe'); words.add_argument('--language', default='en'); words.add_argument('--model')
    words.set_defaults(run=cmd_transcribe)
    frame = sub.add_parser('frame'); frame.add_argument('which', choices=['camera', 'screen'])
    frame.add_argument('--at', type=float, default=5); frame.set_defaults(run=cmd_frame)
    demo = sub.add_parser('demo'); demo.add_argument('action', choices=['prepare', 'approve'])
    demo.add_argument('--yes', action='store_true'); demo.set_defaults(run=cmd_demo)
    args = parser.parse_args()
    try:
        args.run(args)
    except (ValueError, RuntimeError) as error:
        sys.exit(str(error))


if __name__ == '__main__':
    # `python3 studio.py` may be any Python on the Mac. The dependencies live in .venv.
    own_python = installed_python(ROOT, sys.prefix)
    if own_python:
        os.execv(own_python, [own_python, str(ROOT / 'studio.py'), *sys.argv[1:]])
    main()
