"""The n8n form a user fills in: one plain field per variable, a video upload, and the script in sight."""
import argparse
import contextlib
import io
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import studio
from app import server
from tests import fixtures

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = {'intro': {'text': 'Hey {firstName}, I saw you are a {job_title} in {location}. Bye {firstName}.'},
            'demo': {'text': 'This is the tool.'}}


def built(template=TEMPLATE):
    base = json.loads((ROOT / 'n8n/workflow.json').read_text())
    workflow = studio.lead_workflow(base, template)
    return workflow, {n['name']: n for n in workflow['nodes']}


class LeadForm(unittest.TestCase):
    def test_each_variable_is_its_own_plain_field_followed_by_the_profile_address(self):
        fields = built()[1]['New lead']['parameters']['formFields']['values']
        self.assertEqual([f['fieldLabel'] for f in fields],
                         ['First name', 'Job title', 'Location', 'LinkedIn profile URL'])
        self.assertTrue(all(f['requiredField'] for f in fields))
        self.assertNotIn('file', [f.get('fieldType') for f in fields])

    def test_the_form_says_what_submitting_does_without_a_copy_of_the_script_that_would_go_stale(self):
        text = built()[1]['New lead']['parameters']['formDescription']
        self.assertIn('public Loom', text)
        self.assertNotIn('I saw you', text)

    def test_the_intro_is_edited_in_its_own_node_using_the_form_field_names(self):
        field = built()[1]['Intro script']['parameters']['assignments']['assignments'][0]
        self.assertEqual((field['name'], field['type']), ('script', 'string'))
        self.assertEqual(field['value'], 'Hey {First name}, I saw you are a {Job title} in {Location}. Bye {First name}.')

    def test_the_demo_text_is_readable_on_the_canvas_with_how_to_change_the_intro(self):
        note = built()[1]['Your script']['parameters']['content']
        self.assertIn(TEMPLATE['demo']['text'], note)
        self.assertIn('Intro script', note)
        self.assertIn('Publish', note)

    @unittest.skipUnless(shutil.which(studio.node22()), 'needs node')
    def test_every_form_field_reaches_create_lead_including_one_added_later_in_n8n(self):
        expression = built()[1]['Create lead']['parameters']['jsonBody']
        self.assertTrue(expression.startswith('={{') and expression.endswith('}}'))
        typed = {'First name': 'Ana', 'Company': 'Acme', 'LinkedIn profile URL': 'typed address',
                 'submittedAt': 'now', 'formMode': 'production'}
        items = {'New lead': typed, 'Record LinkedIn scroll': {'pageRecording': 'abc.mp4', 'url': 'clean address'},
                 'Intro script': {'script': 'Hey {First name} of {Company}.'}}
        script = (f'const items = {json.dumps(items)};'
                  'const $ = (name) => ({ first: () => ({ json: items[name] }) });'
                  f'console.log(JSON.stringify({expression[3:-2]}))')
        out = subprocess.run([studio.node22(), '-e', script], capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), {'variables': {'First name': 'Ana', 'Company': 'Acme'},
                                           'script': 'Hey {First name} of {Company}.', 'pageRecording': 'abc.mp4',
                                           'url': 'clean address'})

    def test_the_scroll_is_recorded_before_anything_paid_and_has_time_to_finish(self):
        workflow, nodes = built()
        follows = lambda name: workflow['connections'][name]['main'][0][0]['node']
        order = ['New lead']
        while order[-1] != 'HeyGen · intro voice':
            order.append(follows(order[-1]))
        self.assertEqual(order[1:-1], ['Intro script', 'Record LinkedIn scroll', 'Create lead'])
        step = nodes['Record LinkedIn scroll']['parameters']
        # The script and values go along, so a mistake is refused before the profile is visited.
        for part in ("$('New lead').first().json['LinkedIn profile URL']", "$('Intro script').first().json.script",
                     'variables:'):
            self.assertIn(part, step['jsonBody'])
        # A real profile took 130 s. n8n must outlast the worker, which reports why a capture failed.
        self.assertGreaterEqual(server.CAPTURE_TIMEOUT, 300)
        self.assertGreater(step['options']['timeout'], (server.CAPTURE_TIMEOUT + 15) * 1000)

    def test_each_install_gets_its_own_secret_form_address(self):
        # Anyone who knows the address can submit the form, so it cannot be the one published with the code.
        packaged = {n.get('webhookId') for n in json.loads((ROOT / 'n8n/workflow.json').read_text())['nodes']}
        first, second = (built()[0]['nodes'] for _ in range(2))
        for name in ('New lead', 'Wait 10 s'):
            ids = [next(n['webhookId'] for n in nodes if n['name'] == name) for nodes in (first, second)]
            self.assertNotEqual(*ids)
            self.assertFalse(set(ids) & packaged, name)

    def test_two_variables_that_would_share_a_label_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'First name'):
            built({'intro': {'text': '{firstName} {first_name}'}, 'demo': {'text': 'x'}})

    def test_a_variable_already_written_as_words_keeps_its_spelling(self):
        fields = built({'intro': {'text': 'Hi {NPI number}'}, 'demo': {'text': 'x'}})[1]['New lead']
        self.assertEqual(fields['parameters']['formFields']['values'][0]['fieldLabel'], 'NPI number')

    def test_a_name_already_capitalised_is_kept_and_code_style_names_become_words(self):
        self.assertEqual([studio.field_label(n) for n in ('ATS', 'Company', 'company', 'openRole', 'open_role')],
                         ['ATS', 'Company', 'Company', 'Open role', 'Open role'])

    def test_names_differing_only_by_capitals_are_refused_since_they_would_share_a_value(self):
        with self.assertRaisesRegex(ValueError, 'rename'):
            built({'intro': {'text': '{Open Role} {Open role}'}, 'demo': {'text': 'x'}})


class RunningSetupAgain(unittest.TestCase):
    """The script and form belong to the user once installed: setup must not silently reset them."""

    def imports(self, **options):
        done, self.printed = [], io.StringIO()

        def run(command, **_):
            done.extend(part.split(':')[1] for part in command if part.startswith('import:'))
            if 'import:workflow' in command:
                workflow = json.loads(Path(command[-1].split('=', 1)[1]).read_text())
                self.installed = {n['name']: n for n in workflow['nodes']}

        with mock.patch.object(studio.config, 'DATA', self.data), \
                mock.patch.object(studio.config, 'TEMPLATE', self.template), \
                mock.patch.object(studio.config, 'read_secret', lambda name: 'k'), \
                mock.patch.object(studio.config, 'token', lambda name: 't'), \
                mock.patch.object(studio, 'uploader_token', lambda: 'u'), \
                mock.patch.object(studio, 'service_env', dict), \
                mock.patch.object(studio, 'n8n_command', lambda: ['n8n']), \
                mock.patch.object(studio.subprocess, 'run', run), \
                contextlib.redirect_stdout(self.printed):
            studio.cmd_n8n_setup(argparse.Namespace(**{'replace': False, **options}))
        return done

    def test_only_an_explicit_replace_overwrites_an_installed_workflow(self):
        with tempfile.TemporaryDirectory() as d:
            self.data = Path(d)
            self.template = fixtures.template_folder(self.data / 'template')
            self.assertEqual(self.imports(), ['credentials', 'workflow'])
            self.assertEqual(self.imports(), ['credentials'])
            self.assertEqual(self.imports(replace=True), ['credentials', 'workflow'])

    def test_setup_always_ends_with_the_two_links_the_user_needs(self):
        with tempfile.TemporaryDirectory() as d:
            self.data = Path(d)
            self.template = fixtures.template_folder(self.data / 'template')
            links = []
            for _ in range(2):
                self.imports()
                links.append(re.search(rf'http://127.0.0.1:{studio.config.N8N_PORT}/form/([0-9a-f-]{{36}})',
                                       self.printed.getvalue())[1])
                self.assertIn('/workflow/loomPersonalizer1', self.printed.getvalue())
            self.assertEqual(links[0], links[1])
            self.assertEqual(links[0], self.installed['New lead']['webhookId'])


if __name__ == '__main__':
    unittest.main()
