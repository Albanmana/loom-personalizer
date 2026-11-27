import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import studio
from app import providers, server
from tests import fixtures


def fake_fal(log, fail_result=False):
    """A stand-in for the fal_client package, which records what would be paid for."""
    module = types.ModuleType('fal_client.client')

    class Completed:
        pass

    class SyncClient:
        def __init__(self, key, default_timeout):
            log.append(('key', key))

        def upload_file(self, path, **_):
            self.last = Path(path)
            if path.suffix == '.mp4':
                log.append(('video', path))
            return 'https://v3.fal.media/x'

        def submit(self, *_, **__):
            log.append(('submit',))
            return types.SimpleNamespace(request_id=f'request-{sum(1 for e in log if e[0] == "submit")}')

        def status(self, *_):
            return Completed()

        def result(self, *_):
            if fail_result:
                raise KeyError('no face found')
            return {'video': {'url': 'https://v3.fal.media/result.mp4'}}

    module.Completed, module.SyncClient = Completed, SyncClient
    module.StorageSettings = lambda **_: None
    return {'fal_client': types.ModuleType('fal_client'), 'fal_client.client': module}


class LipsyncResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = self.dir = Path(self.tmp.name)
        fixtures.video(d / 'a.mp4', 2)
        fixtures.ff('-i', d / 'a.mp4', '-vf', 'hflip', '-c:v', 'libx264', '-preset', 'ultrafast', d / 'b.mp4')
        fixtures.speech_like_wav(d / 'voice.wav', 2)

    def run_sync(self, log, video, fail=False, key=lambda: 'k'):
        def download(url, out, hosts):
            out.write_bytes((self.dir / video).read_bytes())
        with mock.patch.dict(sys.modules, fake_fal(log, fail)), mock.patch.object(providers, 'download', download):
            providers.fal_lipsync(key)(self.dir / video, self.dir / 'voice.wav', self.dir / 'out.mp4',
                                       self.dir / 'state.json')

    def submits(self, log):
        return sum(1 for entry in log if entry[0] == 'submit')

    def test_same_inputs_resume_without_paying_again(self):
        log = []
        self.run_sync(log, 'a.mp4')
        self.run_sync(log, 'a.mp4')
        self.assertEqual(self.submits(log), 1)

    def test_changed_face_footage_of_the_same_length_is_synced_again(self):
        log = []
        self.run_sync(log, 'a.mp4')
        self.run_sync(log, 'b.mp4')
        self.assertEqual(self.submits(log), 2)

    def test_a_failed_request_is_not_resumed_forever(self):
        log = []
        with self.assertRaisesRegex(RuntimeError, 'Fal failed'):
            self.run_sync(log, 'a.mp4', fail=True)
        self.run_sync(log, 'a.mp4')
        self.assertEqual(self.submits(log), 2)

    def test_the_key_is_read_when_the_lip_sync_starts(self):
        saved = {'key': 'old'}
        lipsync_key = lambda: saved['key']
        providers.fal_lipsync(lipsync_key)
        saved['key'] = 'new'
        log = []
        self.run_sync(log, 'a.mp4', key=lipsync_key)
        self.assertIn(('key', 'new'), log)


class RestartTests(unittest.TestCase):
    def test_jobs_cut_off_by_a_restart_become_failed_so_they_can_be_run_again(self):
        with tempfile.TemporaryDirectory() as d:
            jobs = Path(d) / 'jobs'
            for job_id, status in (('a' * 32, 'rendering'), ('b' * 32, 'queued'), ('c' * 32, 'complete')):
                (jobs / job_id).mkdir(parents=True)
                (jobs / job_id / 'job.json').write_text(json.dumps({'jobId': job_id, 'status': status}))
            app = server.App(template_dir=d, jobs_dir=jobs, loom_dir=d, worker_token='w',
                             download=None, lipsync=None, capture=None)
            self.assertEqual([app.state(x * 32)['status'] for x in 'abc'], ['failed', 'failed', 'complete'])
            self.assertIn('restart', app.state('a' * 32)['error'])
            self.assertIn('Do not submit the lead again', app.state('a' * 32)['error'])


class InterpreterTests(unittest.TestCase):
    def test_commands_move_into_the_installed_environment(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(studio.installed_python(Path(d), '/usr'))
            python = Path(d) / '.venv/bin/python'
            python.parent.mkdir(parents=True)
            python.touch()
            self.assertEqual(studio.installed_python(Path(d), '/usr'), str(python))
            self.assertIsNone(studio.installed_python(Path(d), str(Path(d) / '.venv')), 'already inside')


if __name__ == '__main__':
    unittest.main()
