import json
import tempfile
import unittest
from pathlib import Path

from app import media, pipeline, providers, template
from tests import fixtures


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.t = {'voiceId': 'v', 'intro': {'start': 0, 'end': 3, 'text': 'Hey {firstName} from {company}.'},
                  'demo': {'start': 3, 'end': 9, 'text': 'Demo.', 'anchors': []},
                  'recordings': {'camera': 'c', 'screen': 's', 'screenOffset': 1.0},
                  'camera': {'crop': [10, 10, 0, 0]}, 'screen': {'crop': [16, 9, 0, 0]},
                  'bubble': {'x': 1, 'y': 1, 'size': 232}}

    def test_variables_come_from_the_intro_text(self):
        self.assertEqual(template.variables(self.t), ['firstName', 'company'])

    def test_render_fills_every_variable(self):
        self.assertEqual(template.render_intro(self.t, {'firstName': ' Ana ', 'company': 'Acme', 'extra': 'x'}),
                         'Hey Ana from Acme.')

    def test_missing_or_empty_variable_is_refused_before_any_paid_call(self):
        with self.assertRaisesRegex(ValueError, 'company'):
            template.render_intro(self.t, {'firstName': 'Ana', 'company': '  '})

    def test_demo_text_cannot_contain_variables(self):
        self.t['demo']['text'] = 'Hi {firstName}'
        self.assertTrue(any('demo' in p for p in template.problems(self.t)))

    def test_valid_template_has_no_problem(self):
        self.assertEqual(template.problems(self.t), [])

    def test_unfinished_template_lists_what_is_missing(self):
        self.t['voiceId'] = ''
        self.t['intro']['end'] = 0
        found = ' '.join(template.problems(self.t))
        self.assertIn('voiceId', found)
        self.assertIn('intro', found)


class DemoAnchorTests(unittest.TestCase):
    def test_anchors_move_the_screen_but_leave_the_paid_face_input_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            folder = fixtures.template_folder(Path(d) / 'template')
            fixtures.speech_like_wav(folder / 'demo-voice.wav', 5)
            sent = []

            def lipsync(video, audio, out, state):
                sent.append((media.file_hash(video), media.file_hash(audio)))
                out.write_bytes(video.read_bytes())

            pipeline.prepare_demo(folder, folder / 'demo-voice.wav', lipsync)
            screen = media.file_hash(folder / 'demo-work/screen.mp4')
            data = json.loads((folder / 'template.json').read_text())
            data['demo']['anchors'] = [[5.0, 3.0]]
            (folder / 'template.json').write_text(json.dumps(data))
            pipeline.prepare_demo(folder, folder / 'demo-voice.wav', lipsync)

            self.assertEqual(sent[0], sent[1])
            self.assertNotEqual(screen, media.file_hash(folder / 'demo-work/screen.mp4'))


class LeadLipSyncTests(unittest.TestCase):
    def test_the_paid_lead_lip_sync_is_kept_with_the_job_for_a_retry(self):
        with tempfile.TemporaryDirectory() as d:
            folder = fixtures.template_folder(Path(d) / 'template')
            fixtures.speech_like_wav(folder / 'demo-voice.wav', 4)

            def lipsync(video, audio, out, state):
                out.write_bytes(video.read_bytes())

            pipeline.prepare_demo(folder, folder / 'demo-voice.wav', lipsync)
            pipeline.approve_demo(folder)
            job = Path(d) / 'job'
            job.mkdir()
            fixtures.speech_like_wav(job / 'voice.wav', 1.5, seed=5)
            asked = []

            def paid(video, audio, out, state):
                asked.append(Path(out))
                lipsync(video, audio, out, state)

            pipeline.render_lead(folder, job, lambda seconds, out: fixtures.video(out, seconds, '1920x1080'), paid)
            self.assertEqual(asked, [job / 'face-synced.mp4'])
            self.assertTrue(asked[0].is_file())


class FinishedLipSyncTests(unittest.TestCase):
    def test_a_finished_result_on_disk_is_reused_without_calling_fal(self):
        with tempfile.TemporaryDirectory() as d:
            video, audio, out, state = (Path(d) / n for n in ('face.mp4', 'voice.wav', 'synced.mp4', 'state.json'))
            for path in (video, audio, out):
                path.write_text(path.name)
            state.write_text(json.dumps({'inputs': [media.file_hash(video), media.file_hash(audio)],
                                         'requestId': 'r1', 'output': media.file_hash(out)}))

            def no_key():
                raise AssertionError('Fal must not be contacted')

            providers.fal_lipsync(no_key)(video, audio, out, state)
            self.assertEqual(out.read_text(), 'synced.mp4')


class TimingTests(unittest.TestCase):
    def test_one_overall_speed_without_anchors(self):
        self.assertEqual(media.plan_segments(3.0, 9.0, 150, []), [(3.0, 9.0, 150)])

    def test_anchors_split_the_demo_and_keep_the_exact_frame_count(self):
        parts = media.plan_segments(3.0, 9.0, 150, [[5.0, 1.0], [7.0, 4.0]])
        self.assertEqual(parts, [(3.0, 5.0, 30), (5.0, 7.0, 90), (7.0, 9.0, 30)])

    def test_anchor_outside_the_demo_is_refused(self):
        with self.assertRaises(ValueError):
            media.plan_segments(3.0, 9.0, 150, [[12.0, 1.0]])

    def test_offset_between_two_recordings_is_found_without_extra_libraries(self):
        with tempfile.TemporaryDirectory() as d:
            fixtures.speech_like_wav(Path(d) / 'a.wav', 20)
            fixtures.speech_like_wav(Path(d) / 'b.wav', 20, delay=1.52)
            offset, confidence = media.find_offset(Path(d) / 'a.wav', Path(d) / 'b.wav')
        self.assertAlmostEqual(offset, 1.52, delta=0.02)
        self.assertGreater(confidence, 3)

    def test_unrelated_audio_has_low_confidence(self):
        with tempfile.TemporaryDirectory() as d:
            fixtures.speech_like_wav(Path(d) / 'a.wav', 20, seed=1)
            fixtures.speech_like_wav(Path(d) / 'b.wav', 20, seed=2)
            _, confidence = media.find_offset(Path(d) / 'a.wav', Path(d) / 'b.wav')
        self.assertLess(confidence, 2)


class PipelineTests(unittest.TestCase):
    """The real FFmpeg graphs, with stand-ins for HeyGen, Fal and the page recording."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = fixtures.template_folder(Path(cls.tmp.name) / 'template')
        cls.synced = []
        cls.paged = []

        def lipsync(video, audio, out, state):
            cls.synced.append(media.duration(audio))
            fixtures.ff('-i', video, '-c', 'copy', out)

        def page_video(seconds, out):
            cls.paged.append(seconds)
            fixtures.video(out, seconds, '1920x1080')

        cls.lipsync, cls.page_video = staticmethod(lipsync), staticmethod(page_video)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_1_lead_is_refused_until_the_demo_is_approved(self):
        job = Path(self.tmp.name) / 'early'
        job.mkdir()
        fixtures.speech_like_wav(job / 'voice.wav', 2)
        with self.assertRaisesRegex(ValueError, 'demo'):
            pipeline.render_lead(self.dir, job, self.page_video, self.lipsync)
        self.assertEqual(self.synced, [])

    def test_2_demo_then_lead(self):
        fixtures.speech_like_wav(self.dir / 'demo-voice.wav', 5, seed=3)
        pipeline.prepare_demo(self.dir, self.dir / 'demo-voice.wav', self.lipsync)
        pipeline.approve_demo(self.dir)
        self.synced.clear()
        job = Path(self.tmp.name) / 'lead'
        job.mkdir()
        fixtures.speech_like_wav(job / 'voice.wav', 2, seed=4)

        result = pipeline.render_lead(self.dir, job, self.page_video, self.lipsync)

        intro_frames = result['introFrames']
        self.assertAlmostEqual(intro_frames / 30, 2 + pipeline.JOIN_PAUSE, delta=0.05)
        self.assertEqual(len(self.synced), 1, 'only the intro is lip-synced for a lead')
        self.assertAlmostEqual(self.synced[0], intro_frames / 30, delta=0.05)
        self.assertAlmostEqual(self.paged[0], intro_frames / 30, delta=0.001)
        info = media.streams(job / 'final.mp4')
        self.assertEqual((info['video']['width'], info['video']['height']), (1920, 1080))
        self.assertEqual(int(info['video']['nb_frames']), intro_frames + 150)
        self.assertAlmostEqual(float(info['audio']['duration']), (intro_frames + 150) / 30, delta=0.08)
        media.run(['ffmpeg', '-v', 'error', '-xerror', '-i', job / 'final.mp4', '-f', 'null', '-'], 'decode')

    def test_3_changing_the_template_cancels_the_approval(self):
        path = self.dir / 'template.json'
        original = path.read_text()
        changed = json.loads(original)
        changed['demo']['text'] = 'Different words.'
        path.write_text(json.dumps(changed))
        try:
            self.assertFalse(pipeline.demo_is_approved(self.dir))
        finally:
            path.write_text(original)

    def test_4_changing_only_the_intro_keeps_the_approval(self):
        path = self.dir / 'template.json'
        changed = json.loads(path.read_text())
        changed['intro']['text'] = 'Hello {firstName}!'
        path.write_text(json.dumps(changed))
        self.assertTrue(pipeline.demo_is_approved(self.dir))


if __name__ == '__main__':
    unittest.main()
