"""Where things live. Everything private stays inside this folder's data/ directory."""
import os
import secrets
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get('LP_DATA') or ROOT / 'data')
SECRETS = DATA / 'secrets'
TEMPLATE = DATA / 'template'
JOBS = DATA / 'jobs'
LOOM_CONFIG = DATA / 'loom'
LINKEDIN = DATA / 'linkedin'
WORKER_PORT = int(os.environ.get('LP_WORKER_PORT', 8896))
UPLOADER_PORT = int(os.environ.get('LP_UPLOADER_PORT', 8892))
N8N_PORT = int(os.environ.get('LP_N8N_PORT', 5690))


def node22() -> str:
    for candidate in (os.environ.get('LP_NODE'), '/opt/homebrew/opt/node@22/bin/node',
                      '/usr/local/opt/node@22/bin/node'):
        if candidate and Path(candidate).is_file():
            return candidate
    return shutil.which('node') or 'node'


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    return path


def write_secret(name: str, value: str) -> Path:
    path = private_dir(SECRETS) / name
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, 'w') as out:
        out.write(value.strip() + '\n')
    path.chmod(0o600)
    return path


def read_secret(name: str) -> str:
    path = SECRETS / name
    return path.read_text().strip() if path.is_file() else ''


def token(name: str) -> str:
    """A local random token, created on first use."""
    return read_secret(name) or (write_secret(name, secrets.token_hex(32)) and read_secret(name))
