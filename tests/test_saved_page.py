import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from app import media, pipeline, server
from tests import fixtures


class FitTests(unittest.TestCase):
    def fit(self, source_seconds, wanted):
        with tempfile.TemporaryDirectory() as d:
            fixtures.video(Path(d) / 'in.mp4', source_seconds, '1280x800')
            media.fit_page(Path(d) / 'in.mp4', wanted, Path(d) / 'out.mp4')
            info = media.streams(Path(d) / 'out.mp4')
            return info['video']['width'], info['video']['height'], int(info['video']['nb_frames']), 'audio' in info

    def test_a_shorter_recording_is_extended_to_the_intro_length(self):
        self.assertEqual(self.fit(2, 5), (1920, 1080, 150, False))

    def test_a_longer_recording_is_shortened_to_the_intro_length(self):
        self.assertEqual(self.fit(6, 3), (1920, 1080, 90, False))


class SavedRecordingLeadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        template = fixtures.template_folder(root / 'template')
        passthrough = lambda video, audio, out, state: shutil.copyfile(video, out)
        fixtures.speech_like_wav(template / 'demo-voice.wav', 4, seed=3)
        pipeline.prepare_demo(template, template / 'demo-voice.wav', passthrough)
        pipeline.approve_demo(template)
        fixtures.speech_like_wav(root / 'voice.wav', 1.5, seed=5)
        (root / 'page-recordings').mkdir()
        fixtures.video(root / 'page-recordings/saved.mp4', 3, '1920x1080')
        (root / 'outside.mp4').write_bytes(b'x')
        cls.app = server.App(template_dir=template, jobs_dir=root / 'jobs', loom_dir=root / 'loom', worker_token='w', capture=None,
                             lipsync=passthrough,
                             download=lambda url, out: shutil.copyfile(root / 'voice.wav', out))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def lead(self, **extra):
        return self.app.create_lead({'url': 'https://example.com/p',
                                     'variables': {'firstName': 'Ana', 'company': 'Acme'}, **extra})

    def test_a_job_that_disappeared_does_not_stop_later_renders(self):
        self.app.renders.put(('f' * 32, ''))
        job = self.lead(pageRecording='saved.mp4')['jobId']
        self.app.start_render(job, {'audioUrl': 'https://files.heygen.ai/a.wav'})
        for _ in range(150):
            if self.app.state(job)['status'] in ('complete', 'failed'):
                break
            time.sleep(0.2)
        self.assertEqual(self.app.state(job)['status'], 'complete')

    def test_a_lead_is_rendered_over_its_saved_recording(self):
        job = self.lead(pageRecording=' saved.mp4 ')['jobId']
        self.app.start_render(job, {'audioUrl': 'https://files.heygen.ai/a.wav'})
        for _ in range(150):
            state = self.app.state(job)
            if state['status'] in ('complete', 'failed'):
                break
            time.sleep(0.2)
        self.assertEqual(state['status'], 'complete', state)

    def test_only_files_inside_the_recordings_folder_are_accepted_before_any_cost(self):
        for name in ('', 'missing.mp4', '../outside.mp4', str(Path(self.tmp.name) / 'outside.mp4'), '..'):
            with self.assertRaises(server.Refused, msg=name) as caught:
                self.lead(pageRecording=name)
            self.assertEqual(caught.exception.status, 422)


if __name__ == '__main__':
    unittest.main()
