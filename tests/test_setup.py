"""setup.sh run for real against stand-ins for brew, node and npm."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import studio
from app import config

ROOT = Path(__file__).resolve().parent.parent

# `node` fails to load sqlite3 until the native binary exists.
NODE = '#!/bin/bash\n[ -f "$FAKE/sqlite-built" ]\n'
# `npm` behaves like a machine with `ignore-scripts=true` in ~/.npmrc: only an explicit override builds.
NPM = '''#!/bin/bash
echo "$*" >> "$FAKE/npm.log"
case "$1" in
  install) mkdir -p data/tools/node_modules/n8n/bin && touch data/tools/node_modules/n8n/bin/n8n ;;
  rebuild) [[ "$*" == *--ignore-scripts=false* ]] && touch "$FAKE/sqlite-built" ;;
esac
'''


def script(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


class SetupWithInstallScriptsDisabled(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.fake = Path(temp.name).resolve()
        self.folder = self.fake / 'package'
        self.folder.mkdir()
        shutil.copy(ROOT / 'setup.sh', self.folder)
        script(self.fake / 'bin/brew', '#!/bin/bash\n[ "$1" = --prefix ] && echo "$FAKE"; exit 0\n')
        script(self.fake / 'bin/node', NODE)
        script(self.fake / 'bin/npm', NPM)
        script(self.folder / '.venv/bin/python', '#!/bin/bash\n')
        script(self.folder / '.venv/bin/pip', '#!/bin/bash\n')
        (self.folder / 'data/models').mkdir(parents=True)
        (self.folder / 'data/models/ggml-small.en.bin').touch()

    def run_setup(self) -> list[str]:
        env = {**os.environ, 'FAKE': str(self.fake), 'PATH': f'{self.fake}/bin:/usr/bin:/bin'}
        result = subprocess.run([str(self.folder / 'setup.sh')], env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return (self.fake / 'npm.log').read_text().splitlines()

    def test_builds_only_the_sqlite_module_n8n_needs(self):
        calls = self.run_setup()
        self.assertTrue((self.fake / 'sqlite-built').exists(), calls)
        overrides = [c for c in calls if '--ignore-scripts=false' in c]
        self.assertEqual(len(overrides), 1, calls)
        self.assertTrue(overrides[0].startswith('rebuild ') and overrides[0].endswith(' sqlite3'), overrides)

    def test_running_again_repairs_an_install_that_already_has_n8n(self):
        (self.folder / 'data/tools/node_modules/n8n/bin').mkdir(parents=True)
        (self.folder / 'data/tools/node_modules/n8n/bin/n8n').touch()
        self.run_setup()
        self.assertTrue((self.fake / 'sqlite-built').exists())


class SpeechModelDownload(SetupWithInstallScriptsDisabled):
    def test_a_download_that_does_not_match_is_not_installed(self):
        (self.folder / 'data/models/ggml-small.en.bin').unlink()
        script(self.fake / 'bin/curl', '#!/bin/bash\nwhile [ "$1" != -o ]; do shift; done; echo tampered > "$2"\n')
        env = {**os.environ, 'FAKE': str(self.fake), 'PATH': f'{self.fake}/bin:/usr/bin:/bin'}
        result = subprocess.run([str(self.folder / 'setup.sh')], env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.folder / 'data/models/ggml-small.en.bin').exists())

    test_builds_only_the_sqlite_module_n8n_needs = None
    test_running_again_repairs_an_install_that_already_has_n8n = None


class DoctorSeesABrokenInstall(unittest.TestCase):
    def check(self, sqlite_source: str) -> bool:
        with tempfile.TemporaryDirectory() as temp:
            modules = Path(temp) / 'tools/node_modules'
            script(modules / 'n8n/bin/n8n', '')
            script(modules / 'sqlite3/index.js', sqlite_source)
            env = {k: v for k, v in os.environ.items() if k != 'LP_N8N_BIN'}
            with mock.patch.object(config, 'DATA', Path(temp)), mock.patch.dict(os.environ, env, clear=True):
                return studio.n8n_installed()

    @unittest.skipUnless(shutil.which(studio.node22()), 'needs node')
    def test_n8n_is_not_ready_when_its_database_module_cannot_load(self):
        self.assertFalse(self.check('throw new Error("Could not locate the bindings file")'))
        self.assertTrue(self.check('module.exports = {}'))


class StopWaits(unittest.TestCase):
    def test_stop_returns_only_once_the_service_has_exited(self):
        slow = ('import signal,sys,time\n'
                'signal.signal(signal.SIGTERM, lambda *a: (time.sleep(1.5), sys.exit(0)))\n'
                'print("ready", flush=True)\ntime.sleep(60)')
        service = subprocess.Popen([sys.executable, '-c', slow], stdout=subprocess.PIPE, start_new_session=True)
        self.addCleanup(service.stdout.close)
        self.addCleanup(service.kill)
        service.stdout.readline()
        threading.Thread(target=service.wait, daemon=True).start()
        started = subprocess.run(['ps', '-o', 'lstart=', '-p', str(service.pid)], capture_output=True, text=True)
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(config, 'DATA', Path(temp)), \
                mock.patch.object(studio, '_services', lambda: {'slow': []}):
            (Path(temp) / 'run').mkdir()
            (Path(temp) / 'run/slow.json').write_text(
                json.dumps({'pid': service.pid, 'started': started.stdout.strip()}))
            studio.cmd_stop(None)
        self.assertIsNotNone(service.returncode, 'still shutting down, so a restart would find its port taken')


if __name__ == '__main__':
    unittest.main()
