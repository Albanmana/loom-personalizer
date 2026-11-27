import json
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import pipeline, server
from tests import fixtures

WORKER = 'worker-token'


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.template = fixtures.template_folder(root / 'template')

        def lipsync(video, audio, out, state):
            fixtures.ff('-i', video, '-c', 'copy', out)

        fixtures.speech_like_wav(cls.template / 'demo-voice.wav', 4, seed=3)
        pipeline.prepare_demo(cls.template, cls.template / 'demo-voice.wav', lipsync)
        pipeline.approve_demo(cls.template)
        fixtures.speech_like_wav(root / 'heygen.wav', 1.5, seed=5)

        def download(url, out):
            out.write_bytes((root / 'heygen.wav').read_bytes())

        def capture(url, out):
            if 'walled' in url:
                raise RuntimeError('LinkedIn asks you to sign in.')
            fixtures.video(out, 3, '1600x900')

        cls.app = server.App(template_dir=cls.template, jobs_dir=root / 'jobs', loom_dir=root / 'loom',
                             worker_token=WORKER, download=download, lipsync=lipsync, capture=capture)
        (root / 'page-recordings').mkdir()
        fixtures.video(root / 'page-recordings/saved.mp4', 3, '1920x1080')
        (root / 'page-recordings/broken.mp4').write_bytes(b'not a video')
        cls.http = server.make_server(cls.app, port=0)
        cls.base = f'http://127.0.0.1:{cls.http.server_address[1]}'
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.tmp.cleanup()

    def call(self, method, path, body=None, token=WORKER, headers=None, raw=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        request = Request(self.base + path, data=data, method=method,
                          headers={'Authorization': f'Bearer {token}', **(headers or {})})
        try:
            with urlopen(request, timeout=30) as response:
                payload = response.read()
                kind = response.headers.get('content-type', '')
                return response.status, json.loads(payload) if 'json' in kind and payload else payload
        except HTTPError as error:
            return error.code, json.loads(error.read() or b'{}')

    def test_the_token_is_required(self):
        self.assertEqual(self.call('POST', '/api/leads', {}, token='nope')[0], 401)

    def test_a_negative_length_is_refused_at_once(self):
        with socket.create_connection(('127.0.0.1', self.http.server_address[1]), timeout=5) as connection:
            connection.sendall(b'POST /api/leads HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: -1\r\n\r\n')
            self.assertIn(b' 413 ', connection.recv(200))

    def test_another_host_name_is_refused(self):
        self.assertEqual(self.call('POST', '/api/leads', {}, headers={'Host': 'evil.example'})[0], 403)

    def test_there_are_no_browser_extension_routes(self):
        for method, path in (('GET', '/ext/next'), ('POST', '/ext/ping'), ('POST', '/ext/loom-session')):
            self.assertIn(self.call(method, path, {} if method == 'POST' else None)[0], (401, 404), path)
        self.assertEqual(self.call('GET', '/health')[1], {'ok': True})

    def test_a_profile_address_becomes_a_recording_a_lead_can_use(self):
        status, recorded = self.call('POST', '/api/page-recordings', {'url': 'https://www.linkedin.com/in/jane/'})
        self.assertEqual(status, 201, recorded)
        self.assertEqual(recorded['url'], 'https://www.linkedin.com/in/jane/')
        self.assertTrue((self.app.page_dir / recorded['pageRecording']).is_file())
        status, lead = self.call('POST', '/api/leads', {'pageRecording': recorded['pageRecording'],
                                                        'url': recorded['url'],
                                                        'variables': {'firstName': 'A', 'company': 'B'}})
        self.assertEqual(status, 201, lead)

    def test_a_failed_capture_shows_its_reason_and_leaves_nothing(self):
        before = sorted(self.app.page_dir.iterdir())
        status, body = self.call('POST', '/api/page-recordings', {'url': 'https://www.linkedin.com/in/walled/'})
        self.assertEqual(status, 422)
        self.assertIn('sign in', body['detail'])
        self.assertEqual(sorted(self.app.page_dir.iterdir()), before)

    def test_a_script_mistake_is_refused_before_the_profile_is_visited(self):
        before = sorted(self.app.page_dir.iterdir())
        status, body = self.call('POST', '/api/page-recordings', {
            'url': 'https://www.linkedin.com/in/jane/', 'script': 'Hi {Nickname}', 'variables': {'First name': 'Ana'}})
        self.assertEqual(status, 422)
        self.assertIn('Nickname', body['detail'])
        self.assertEqual(sorted(self.app.page_dir.iterdir()), before)

    def lead_with_script(self, script, **variables):
        return self.call('POST', '/api/leads', {'pageRecording': 'saved.mp4', 'script': script,
                                                'variables': variables})

    def test_the_intro_sent_by_n8n_is_what_gets_spoken(self):
        status, lead = self.lead_with_script('Hello {First name} from { company }!',
                                             **{'First name': 'Ana', 'Company': 'Acme'})
        self.assertEqual(status, 201, lead)
        self.assertEqual(lead['script'], 'Hello Ana from Acme!')

    def test_a_script_that_cannot_be_filled_is_refused_before_any_paid_step(self):
        for script, word in (('Hi {Nickname}', 'Nickname'), ('', 'empty'), ('Hi {First name', 'brace')):
            status, body = self.lead_with_script(script, **{'First name': 'Ana'})
            self.assertEqual(status, 422, script)
            self.assertIn(word, body['detail'])

    def test_a_lead_without_a_page_recording_is_refused_before_generation(self):
        status, body = self.call('POST', '/api/leads', {'url': 'https://example.com',
                                                        'variables': {'firstName': 'A', 'company': 'B'}})
        self.assertEqual(status, 422)
        self.assertIn('page-recordings', body['detail'])

    def test_missing_variable_and_bad_url_are_refused_before_generation(self):
        status, body = self.call('POST', '/api/leads', {'url': 'https://example.com', 'pageRecording': 'saved.mp4',
                                                        'variables': {'firstName': 'A'}})
        self.assertEqual(status, 422)
        self.assertIn('company', body['detail'])
        status, _ = self.call('POST', '/api/leads', {'url': 'file:///etc/passwd',
                                                     'variables': {'firstName': 'A', 'company': 'B'}})
        self.assertEqual(status, 422)

    def test_variables_can_be_typed_as_lines(self):
        status, body = self.call('POST', '/api/leads', {'url': 'https://example.com/a', 'pageRecording': 'saved.mp4',
                                                        'variables': 'firstName = Ana\ncompany=Acme Inc'})
        self.assertEqual(status, 201)
        self.assertEqual(body['script'], 'Hey Ana, I saw you work at Acme Inc.')
        self.assertEqual(body['voiceId'], 'voice-1')

    def finish(self, recording):
        _, lead = self.call('POST', '/api/leads', {'url': 'https://example.com/p', 'pageRecording': recording,
                                                   'variables': {'firstName': 'Ana', 'company': 'Acme'}})
        job = lead['jobId']
        self.assertEqual(self.call('POST', f'/api/jobs/{job}/render',
                                   {'audioUrl': 'https://files.heygen.ai/a.wav'})[0], 202)
        for _ in range(150):
            _, state = self.call('GET', f'/api/jobs/{job}')
            if state['status'] in ('complete', 'failed'):
                break
            time.sleep(0.2)
        return job, state

    def test_full_lead_with_a_saved_page_recording(self):
        job, state = self.finish('saved.mp4')
        self.assertEqual(state['status'], 'complete', state)
        status, mp4 = self.call('GET', f'/api/jobs/{job}/video')
        self.assertEqual((status, mp4[4:8]), (200, b'ftyp'))

    def test_an_unreadable_page_recording_fails_the_job(self):
        _, state = self.finish('broken.mp4')
        self.assertEqual(state['status'], 'failed', state)
        self.assertTrue(state['error'])


class AudioHostTests(unittest.TestCase):
    def test_only_expected_https_hosts_are_downloaded(self):
        from app import providers
        providers.check_url('https://resource2.heygen.ai/a.wav', providers.HEYGEN_HOSTS)
        for bad in ('http://resource2.heygen.ai/a.wav', 'https://evil.example/a.wav',
                    'https://user:pw@resource2.heygen.ai/a.wav', 'https://heygen.ai.evil.example/a'):
            with self.assertRaises(ValueError, msg=bad):
                providers.check_url(bad, providers.HEYGEN_HOSTS)


if __name__ == '__main__':
    unittest.main()
