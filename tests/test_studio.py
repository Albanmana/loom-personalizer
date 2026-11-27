import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import studio
from app import config

ROOT = Path(__file__).resolve().parent.parent


class PackageTests(unittest.TestCase):
    def test_workflow_links_point_to_real_nodes_and_known_credentials(self):
        workflow = json.loads((ROOT / 'n8n/workflow.json').read_text())
        names = {n['name'] for n in workflow['nodes']}
        for source, outputs in workflow['connections'].items():
            self.assertIn(source, names)
            for branch in outputs['main']:
                for target in branch:
                    self.assertIn(target['node'], names)
        used = {n['credentials']['httpHeaderAuth']['id'] for n in workflow['nodes'] if 'credentials' in n}
        made = {c['id'] for c in studio.n8n_credentials('h', 'w', 'u')}
        self.assertEqual(used, made)

    def test_workflow_carries_no_fixed_voice_or_prospect(self):
        text = (ROOT / 'n8n/workflow.json').read_text().lower()
        self.assertNotRegex(text, r'(?<![a-f0-9-])[a-f0-9]{32}(?![a-f0-9-])', 'no hard-coded voice ID')
        self.assertNotIn('linkedin.com/in/', text)

    def test_nothing_private_is_read_outside_the_package(self):
        for path in [*ROOT.glob('app/*.py'), ROOT / 'studio.py']:
            text = path.read_text()
            self.assertNotIn('.codex', text, path.name)
            self.assertNotIn("'.env'", text, path.name)
        self.assertEqual(config.DATA.parent, ROOT)

    def test_services_use_their_own_ports_and_folders(self):
        env = studio.service_env()
        self.assertEqual(env['LOOM_CONFIG_DIR'], str(config.LOOM_CONFIG))
        self.assertTrue(env['N8N_USER_FOLDER'].startswith(str(config.DATA)))
        ports = {config.WORKER_PORT, config.UPLOADER_PORT, config.N8N_PORT, int(env['N8N_RUNNERS_BROKER_PORT'])}
        self.assertEqual(len(ports), 4)
        self.assertFalse(ports & {8796, 8792, 5678, 5679}, 'must not collide with an existing install')

    def test_loom_sign_in_saves_the_session_where_the_uploader_reads_it(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(config, 'LOOM_CONFIG', Path(d) / 'loom'), \
                mock.patch.object(studio.subprocess, 'run') as run:
            run.return_value.returncode = 0
            with self.assertRaises(SystemExit) as done:
                studio.cmd_loom(None)
            self.assertEqual(done.exception.code, 0)
            command = run.call_args.args[0]
            self.assertEqual(command[1:], [str(ROOT / 'uploader/loom-login.mjs'),
                                           str(Path(d) / 'loom/session.cookie')])

    def test_nothing_asks_for_a_browser_extension(self):
        for name in ('studio.py', 'app/server.py', 'app/media.py', 'app/pipeline.py', 'n8n/workflow.json',
                     'CLAUDE.md', 'README.md', 'docs/design.md'):
            self.assertNotRegex((ROOT / name).read_text().lower(), r'browser extension|extension_token|extension\.token|\bpair(ing)? code|studio\.py pair', name)
        self.assertFalse((ROOT / 'extension').exists())

    def test_estimate_is_shown_before_paying(self):
        self.assertAlmostEqual(studio.lipsync_cost(60), 5.0)


if __name__ == '__main__':
    unittest.main()
