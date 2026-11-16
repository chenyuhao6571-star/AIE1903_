import base64
import copy
import json
import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT

from backend import ai, criteria, github, main
from backend.schema import Profile, ScoreResponse
from backend.extraction import extract_document


class GitHubTokenTests(unittest.TestCase):
    def test_token_file_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(github.os.environ, {}, clear=True):
            path = Path(directory) / 'github.txt'
            with patch.object(github, 'DEFAULT_TOKEN_FILE', path):
                self.assertIsNone(github.read_token())
                path.write_text('\ufefftest_file_token\n', encoding='utf-8')
                self.assertEqual(github.read_token(), 'test_file_token')
                with patch.dict(github.os.environ, {'GITHUB_TOKEN': 'test_env_token'}):
                    self.assertEqual(github.read_token(), 'test_env_token')
                with patch.dict(github.os.environ, {'GITHUB_TOKEN_FILE': str(path)}):
                    self.assertEqual(github.read_token(), 'test_file_token')
                path.write_text('secret: invalid token', encoding='utf-8')
                with self.assertRaises(github.GitHubError) as error:
                    github.read_token()
                self.assertNotIn('secret', str(error.exception))
                path.write_text('', encoding='utf-8')
                with self.assertRaisesRegex(github.GitHubError, 'empty'):
                    github.read_token()


def cv_bytes(url='https://github.com/example', hidden=False):
    doc = Document()
    paragraph = doc.add_paragraph('Example Candidate. Researcher in robotics and reproducible systems. Education: PhD completed.')
    if hidden:
        rid = doc.part.relate_to(url, RT.HYPERLINK, is_external=True)
        link = OxmlElement('w:hyperlink')
        link.set(qn('r:id'), rid)
        run = OxmlElement('w:r')
        text = OxmlElement('w:t')
        text.text = 'GitHub'
        run.append(text)
        link.append(run)
        paragraph._p.append(link)
    else:
        doc.add_paragraph(url)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def snapshot():
    return {'username': 'example', 'url': 'https://github.com/example', 'fetched_at': '2026-09-30T00:00:00Z',
            'coverage': 'One repository', 'repositories': [], 'warnings': [],
            'pages': [{'number': 1, 'filename': 'GitHub / example', 'source_url': 'https://github.com/example',
                       'text': 'Public repository Example: a reproducible robotics teaching simulation.'}]}


class GitHubFetchTests(unittest.IsolatedAsyncioTestCase):
    def test_profile_discovery_handles_visible_urls_but_not_repository_owners(self):
        self.assertEqual(github.profile_links('GitHub: github.com/Example. https://www.github.com/EXAMPLE/?tab=repositories'), ['https://github.com/example'])
        self.assertEqual(github.profile_links('https://github.com/example/paper https://evil.com/github.com/example https://github.com/login'), [])
        self.assertEqual(github.profile_links('(https://github.com/first) https://github.com/second'), ['https://github.com/first', 'https://github.com/second'])

    def test_hidden_docx_hyperlink_is_discovered(self):
        pages = extract_document(cv_bytes(hidden=True), 'CV.docx')
        self.assertIn('https://github.com/example', pages[0]['links'])
        self.assertNotIn('https://github.com/example', pages[0]['text'])

    def test_hidden_pdf_annotation_is_discovered(self):
        class Annotation(dict):
            def get_object(self): return self
        class Page(dict):
            def extract_text(self): return 'Example Candidate. Education and research experience in robotics and software systems.'
        reader = type('Reader', (), {'is_encrypted': False, 'pages': [Page({'/Annots': [Annotation({'/A': {'/S': '/URI', '/URI': 'https://github.com/example'}})]})]})()
        with patch('backend.extraction.PdfReader', return_value=reader):
            pages = extract_document(b'placeholder PDF', 'CV.pdf')
        self.assertEqual(pages[0]['links'], ['https://github.com/example'])

    def test_only_personal_github_urls_are_allowed(self):
        self.assertEqual(github.username_from_url('https://github.com/Example/'), 'example')
        for url in ['http://github.com/example', 'https://evil.com/example', 'https://github.com.evil.com/example',
                    'https://github.com@localhost/example', 'https://github.com:8080/example',
                    'https://github.com/example/repo', 'https://github.com/%2e%2e',
                    'https://github.com/example?next=http://localhost', 'https://127.0.0.1/example']:
            with self.subTest(url=url), self.assertRaises(github.GitHubError):
                github.username_from_url(url)

    async def test_bounded_public_import_ignores_forks_and_external_links(self):
        calls = []
        def handle(request):
            calls.append(request)
            self.assertEqual(request.url.host, 'api.github.com')
            if request.url.path == '/users/example':
                return httpx.Response(200, json={'type': 'User'})
            if request.url.path.endswith('/repos'):
                repos = [{'name': f'project-{i}', 'owner': {'login': 'example'}, 'description': 'Test',
                          'language': 'Python', 'fork': False, 'private': False} for i in range(9)]
                return httpx.Response(200, json=[{**repos[0], 'fork': True}, {**repos[0], 'private': True}] + repos)
            readme = ('Use the simulation. http://localhost/private\n' * 200).encode()
            return httpx.Response(200, json={'encoding': 'base64', 'content': base64.b64encode(readme).decode()})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch.object(github.httpx, 'AsyncClient', return_value=client):
            result = await github.fetch_snapshot('https://github.com/example')
        self.assertEqual(len(calls), 2 + github.MAX_REPOS)
        self.assertEqual(len(result['repositories']), github.MAX_REPOS)
        self.assertTrue(result['warnings'])
        self.assertTrue(all(r['readme_truncated'] for r in result['repositories']))
        self.assertTrue(all(len(p['text']) < github.MAX_README + 1200 for p in result['pages']))

    async def test_api_errors_and_redirects_are_not_followed(self):
        for status in [301, 401, 403, 404, 429, 500]:
            calls = []
            def handle(request):
                calls.append(request)
                return httpx.Response(status, headers={'Location': 'http://localhost/private'})
            client = httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=False)
            with patch.object(github.httpx, 'AsyncClient', return_value=client):
                with self.subTest(status=status), self.assertRaises(github.GitHubError):
                    await github.fetch_snapshot('https://github.com/example')
            self.assertEqual(len(calls), 1)

    async def test_missing_readme_keeps_metadata(self):
        def handle(request):
            if request.url.path == '/users/example':
                return httpx.Response(200, json={'type': 'User'})
            if request.url.path.endswith('/repos'):
                return httpx.Response(200, json=[{'name': 'repo', 'owner': {'login': 'example'}}])
            return httpx.Response(404)
        with patch.object(github.httpx, 'AsyncClient', return_value=httpx.AsyncClient(transport=httpx.MockTransport(handle))):
            result = await github.fetch_snapshot('https://github.com/example')
        self.assertFalse(result['repositories'][0]['readme_available'])
        self.assertIn('README unavailable', result['pages'][1]['text'])


class GitHubAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = patch.object(main, 'STORAGE', Path(self.temp.name))
        self.storage.start()
        self.client = TestClient(main.app)
        self.client.__enter__()
        self.id = self.client.post('/api/profiles', json={'name': 'Example'}).json()['id']
        self.url = '/api/profiles/' + self.id

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.storage.stop()
        self.temp.cleanup()

    def test_cv_upload_automatically_imports_github_once_without_llm(self):
        with patch.object(main, 'fetch_snapshot', AsyncMock(return_value=snapshot())) as fetch, \
                patch.object(main, 'generate_profile', AsyncMock()) as model:
            response = self.client.post(self.url + '/documents', files={'files': ('CV.docx', cv_bytes(hidden=True))})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()['github']['url'], 'https://github.com/example')
            self.assertEqual(response.json()['github_origin'], 'auto')
            self.client.post(self.url + '/documents', files={'files': ('Statement.docx', cv_bytes('Additional teaching experience.'))})
            fetch.assert_awaited_once_with('https://github.com/example')
            model.assert_not_awaited()
        doc_id = response.json()['documents'][0]['id']
        removed = self.client.delete(self.url + '/documents/' + doc_id)
        self.assertIsNone(removed.json()['github'])

    def test_auto_import_failure_keeps_successfully_uploaded_cv(self):
        with patch.object(main, 'fetch_snapshot', AsyncMock(side_effect=github.GitHubError('GitHub quota exhausted'))):
            response = self.client.post(self.url + '/documents', files={'files': ('CV.docx', cv_bytes())})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(response.json()['documents']), 1)
        self.assertIn('quota', response.json()['github_import_error'])
        self.assertEqual(response.json()['github_discovery']['candidates'], ['https://github.com/example'])

    def test_multiple_accounts_are_not_silently_attributed_to_candidate(self):
        with patch.object(main, 'fetch_snapshot', AsyncMock()) as fetch:
            response = self.client.post(self.url + '/documents', files={'files': ('CV.docx', cv_bytes('https://github.com/first https://github.com/second'))})
            fetch.assert_not_awaited()
        self.assertEqual(len(response.json()['github_discovery']['candidates']), 2)
        self.assertFalse(response.json().get('github'))

    def test_existing_cv_hidden_link_backfill_preserves_stored_text(self):
        path = main.folder(self.id)
        (path / 'old.docx').write_bytes(cv_bytes(hidden=True))
        old_pages = [{'number': 1, 'text': 'Existing extracted text without hyperlink target.'}]
        main.write_json(path / 'old.json', old_pages)
        main.update_job(self.id, documents=[{'id': 'old', 'file': 'old.docx', 'text_file': 'old.json', 'filename': 'CV.docx',
                                           'extension': '.docx', 'characters': 50}], revision=1)
        with patch.object(main, 'fetch_snapshot', AsyncMock(return_value=snapshot())) as fetch:
            asyncio.run(main.discover_github(self.id))
            asyncio.run(main.discover_github(self.id))
            fetch.assert_awaited_once_with('https://github.com/example')
        self.assertEqual(main.read_json(path / 'old.json'), old_pages)
        self.assertEqual(main.read_job(self.id)['github']['username'], 'example')

    def test_import_cache_refresh_remove_and_sources_survive_restart(self):
        with patch.object(main, 'fetch_snapshot', AsyncMock(return_value=snapshot())) as fetch, \
                patch.object(main, 'generate_profile', AsyncMock()) as model:
            first = self.client.put(self.url + '/github', json={'url': 'https://github.com/example'})
            self.assertEqual(first.status_code, 200)
            revision = first.json()['revision']
            again = self.client.put(self.url + '/github', json={'url': 'https://github.com/Example/'})
            self.assertEqual(again.json()['revision'], revision)
            fetch.assert_awaited_once()
            model.assert_not_awaited()
            self.assertEqual(self.client.get('/api/requests').json(), [])
            with TestClient(main.app) as restarted:
                detail = restarted.get(self.url).json()
                self.assertEqual(detail['github']['username'], 'example')
                self.assertEqual(detail['sources'][0]['unit'], 'github')
            pages = self.client.get(self.url + '/text').json()
            self.assertIn('source_url', pages[0])
            refreshed = self.client.put(self.url + '/github', json={'url': 'https://github.com/example', 'refresh': True})
            self.assertEqual(refreshed.json()['revision'], revision + 1)
            removed = self.client.delete(self.url + '/github')
            self.assertIsNone(removed.json()['github'])
            self.assertTrue(list(main.folder(self.id).glob('github-*.json')))
            self.assertEqual(self.client.put(self.url + '/github', json={'url': 'https://github.com/example'}).status_code, 200)

    def test_failed_refresh_preserves_previous_snapshot_and_busy_blocks_import(self):
        with patch.object(main, 'fetch_snapshot', AsyncMock(return_value=snapshot())):
            original = self.client.put(self.url + '/github', json={'url': 'https://github.com/example'}).json()
        with patch.object(main, 'fetch_snapshot', AsyncMock(side_effect=github.GitHubError('Rate limited'))):
            self.assertEqual(self.client.put(self.url + '/github', json={'url': 'https://github.com/example', 'refresh': True}).status_code, 400)
        after = self.client.get(self.url).json()
        self.assertEqual(after['github'], original['github'])
        self.assertEqual(after['revision'], original['revision'])
        main.update_job(self.id, score_status='analyzing')
        with patch.object(main, 'fetch_snapshot', AsyncMock()) as fetch:
            self.assertEqual(self.client.put(self.url + '/github', json={'url': 'https://github.com/example'}).status_code, 409)
            fetch.assert_not_awaited()

    def test_combined_sources_and_import_invalidate_profile_and_score(self):
        main.write_json(main.folder(self.id) / 'doc.json', [{'number': 1, 'text': 'An academic CV'}])
        doc = {'id': 'doc', 'text_file': 'doc.json', 'filename': 'CV.pdf', 'extension': '.pdf', 'characters': 14}
        main.update_job(self.id, documents=[doc], revision=1, analyzed_revision=1, status='completed', score_status='completed')
        main.write_json(main.folder(self.id) / 'profile.json', {'bio': {'name': 'Example', 'source_pages': [1]},
                        'education': [], 'work_experience': [], 'achievements_awards': [], 'publications': []})
        with patch.object(main, 'fetch_snapshot', AsyncMock(return_value=snapshot())):
            self.client.put(self.url + '/github', json={'url': 'https://github.com/example'})
        detail = self.client.get(self.url).json()
        self.assertIsNone(detail['profile'])
        self.assertEqual(detail['score_status'], 'none')
        self.assertEqual([s['number'] for s in detail['sources']], [1, 2])
        text = self.client.get(self.url + '/text').json()
        messages = ai.profile_messages(text)
        self.assertIn('An academic CV', messages[1]['content'])
        self.assertIn('https://github.com/example', messages[1]['content'])

    def test_github_import_obeys_combined_size_limit(self):
        huge = snapshot()
        huge['pages'][0]['text'] = 'x' * (main.MAX_TEXT + 1)
        with patch.object(main, 'fetch_snapshot', AsyncMock(return_value=huge)):
            self.assertEqual(self.client.put(self.url + '/github', json={'url': 'https://github.com/example'}).status_code, 400)
        self.assertEqual(self.client.get(self.url).json()['revision'], 0)


class GitHubModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_cv_link_without_import_does_not_discard_profile(self):
        pages = [{'number': 1, 'text': 'Example. GitHub: https://github.com/example'}]
        profile = {'bio': {'name': 'Example', 'source_pages': [1]}, 'education': [], 'work_experience': [],
                   'achievements_awards': [], 'publications': [], 'links': [{'title': 'GitHub Profile Analysis',
                   'description': 'No repositories were imported. The profile may contain research code.',
                   'url': 'https://github.com/example', 'source_pages': [1]}]}
        with patch.object(ai, 'call_json', AsyncMock(return_value=(json.dumps(profile), {'calls': 1}))) as model:
            result, receipt = await ai.generate_profile(pages)
            self.assertEqual(result.bio.name, 'Example')
            self.assertEqual(result.links, [])
            self.assertEqual(receipt['calls'], 1)
            model.assert_awaited_once()
            self.assertIn('NO imported GitHub sources', model.call_args.args[0][0]['content'])
        profile['bio']['source_pages'] = [999]
        with patch.object(ai, 'call_json', AsyncMock(return_value=(json.dumps(profile), {}))):
            with self.assertRaisesRegex(ai.AIError, r'bio\[0\]: missing or invalid source IDs'):
                await ai.generate_profile(pages)

    async def test_profile_schema_error_identifies_field(self):
        profile = {'bio': {'source_pages': [1]}, 'education': [], 'work_experience': [],
                   'achievements_awards': [], 'publications': []}
        with patch.object(ai, 'call_json', AsyncMock(return_value=(json.dumps(profile), {}))):
            with self.assertRaisesRegex(ai.AIError, 'bio.name: Field required'):
                await ai.generate_profile([{'number': 1, 'text': 'CV'}])

    async def test_one_analysis_call_includes_github_and_checks_links(self):
        pages = [{'number': 1, 'text': 'Candidate CV'}, {**snapshot()['pages'][0], 'number': 2, 'unit': 'github'}]
        profile = {'bio': {'name': 'Example', 'source_pages': [1]}, 'education': [], 'work_experience': [],
                   'achievements_awards': [], 'publications': [], 'links': [{'title': 'GitHub overview',
                   'description': 'A robotics teaching simulation, based on README only.',
                   'url': 'https://github.com/example', 'source_pages': [2]}]}
        with patch.object(ai, 'call_json', AsyncMock(return_value=(Profile.model_validate(profile).model_dump_json(), {}))) as model:
            result, _ = await ai.generate_profile(pages)
            self.assertEqual(len(result.links), 1)
            model.assert_awaited_once()
            self.assertIn('Candidate CV', model.call_args.args[0][1]['content'])
            self.assertIn('reproducible robotics', model.call_args.args[0][1]['content'])
        for mutation in ['url', 'source', 'missing']:
            bad = copy.deepcopy(profile)
            if mutation == 'url': bad['links'][0]['url'] = 'https://evil.com'
            if mutation == 'source': bad['links'][0]['source_pages'] = [1]
            if mutation == 'missing': bad['links'] = []
            with patch.object(ai, 'call_json', AsyncMock(return_value=(Profile.model_validate(bad).model_dump_json(), {}))):
                with self.subTest(mutation=mutation), self.assertRaises(ai.AIError):
                    await ai.generate_profile(pages)

    def test_templates_keep_100_with_five_for_github(self):
        with tempfile.TemporaryDirectory() as storage:
            for t in criteria.list_templates(Path(storage)):
                self.assertEqual(t['max_score'], 100)
                self.assertEqual(next(s['max_score'] for s in t['sections'] if s['id'] == 'github'), 5)

    def test_positive_github_score_requires_imported_evidence(self):
        rubric = criteria.get_template(Path('unused-storage'), 'assistant_professor')
        rubric.sections = [s for s in rubric.sections if s.id == 'github']
        result = ScoreResponse.model_validate({'sections': [{'section_id': 'github', 'score': 2,
                  'status': 'supported', 'rationale': 'Documented repository.', 'gaps': [],
                  'evidence': [{'source_id': 1, 'quote': 'A public research repository'}]}]})
        with self.assertRaisesRegex(ValueError, 'imported GitHub evidence'):
            ai.validate_scores(result, [{'number': 1, 'text': 'A public research repository'}], rubric)
        scored = ai.validate_scores(result, [{'number': 1, 'text': 'A public research repository', 'unit': 'github'}], rubric)
        self.assertEqual(scored['total'], 2)
