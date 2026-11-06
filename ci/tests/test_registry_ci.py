import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error


spec = importlib.util.spec_from_file_location('registry_ci', Path(__file__).resolve().parents[1] / 'registry_ci.py')
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)
SOURCE = 'a' * 40
RENDERER = 'b' * 40
OTHER = 'c' * 40
REPOSITORY = 'https://gitlab.com/rygt-team/ShadGT.git'


def response(value):
    return io.BytesIO(json.dumps(value).encode())


def policy(commit='HEAD', repository=REPOSITORY, branch='ryty-gpu'):
    return {'repository': repository, 'branch': branch, 'commit': commit}


class RendererRevisionTests(unittest.TestCase):
    def test_pinned_revision_uses_source_policy_only(self):
        with patch.object(registry.urllib.request, 'urlopen', return_value=response(policy(RENDERER))) as request:
            self.assertEqual(registry.renderer_revision(SOURCE), RENDERER)
        request.assert_called_once_with(registry.API + '/repository/files/renderer%2Fsource.json/raw?ref=' + SOURCE, timeout=60)

    def test_head_resolves_official_branch_once(self):
        with patch.object(registry.urllib.request, 'urlopen', side_effect=[response(policy()), response({'commit': {'id': RENDERER}})]) as request:
            self.assertEqual(registry.renderer_revision(SOURCE), RENDERER)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[1].args[0], 'https://gitlab.com/api/v4/projects/rygt-team%2FShadGT/repository/branches/ryty-gpu')

    def test_repository_comparison_ignores_case(self):
        with patch.object(registry.urllib.request, 'urlopen', return_value=response(policy(RENDERER, REPOSITORY.upper()))):
            self.assertEqual(registry.renderer_revision(SOURCE), RENDERER)

    def test_invalid_policy_rejected_without_branch_request(self):
        for config in (policy(repository='https://example.com/foreign.git'), policy(branch='main'), policy(commit='abc'), {}, []):
            with self.subTest(config=config), patch.object(registry.urllib.request, 'urlopen', return_value=response(config)) as request:
                with self.assertRaises((ValueError, RuntimeError, TypeError)):
                    registry.renderer_revision(SOURCE)
                self.assertEqual(request.call_count, 1)

    def test_invalid_head_rejected(self):
        with patch.object(registry.urllib.request, 'urlopen', side_effect=[response(policy()), response({'commit': {'id': 'bad'}})]):
            with self.assertRaises((ValueError, RuntimeError)):
                registry.renderer_revision(SOURCE)

    def test_version_includes_both_validated_revisions(self):
        self.assertEqual(registry.version(SOURCE, RENDERER), f'development-{SOURCE}-renderer-{RENDERER}')
        self.assertNotEqual(registry.version(SOURCE, RENDERER), registry.version(SOURCE, OTHER))
        for source, renderer in (('bad', RENDERER), (SOURCE, 'bad')):
            with self.subTest(source=source, renderer=renderer), self.assertRaises(ValueError):
                registry.version(source, renderer)


class ResolveTests(unittest.TestCase):
    def run_resolve(self, markers):
        requests = []
        def open_url(url, timeout):
            requests.append(url)
            if url.endswith('/repository/branches/development'):
                return response({'commit': {'id': SOURCE}})
            channel = 'debug-ci' if '/debug-ci/' in url else 'ci'
            marker = markers[channel]
            if marker is None:
                raise urllib.error.HTTPError(url, 404, 'Not Found', {}, None)
            return response(marker)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'output'
            with patch.dict(os.environ, {'GITHUB_OUTPUT': str(output), 'FORCE_BUILD': 'false'}), patch.object(registry.urllib.request, 'urlopen', side_effect=open_url), patch.object(registry, 'renderer_revision', return_value=RENDERER) as renderer:
                registry.resolve()
            renderer.assert_called_once_with(SOURCE)
            return dict(line.split('=', 1) for line in output.read_text().splitlines()), requests

    def test_existing_pair_skips_build(self):
        markers = {channel: {'commit': SOURCE, 'renderer_commit': RENDERER, 'channel': channel} for channel in registry.CHANNELS}
        output, requests = self.run_resolve(markers)
        self.assertEqual(output, {'commit': SOURCE, 'renderer_commit': RENDERER, 'version': f'development-{SOURCE}-renderer-{RENDERER}', 'needed': 'false'})
        self.assertEqual(sum('/repository/branches/development' in url for url in requests), 1)
        self.assertTrue(all(output['version'] in url for url in requests[1:]))

    def test_missing_pair_requires_build(self):
        output, _ = self.run_resolve({'ci': None, 'debug-ci': None})
        self.assertEqual(output['needed'], 'true')

    def test_mismatching_marker_rejected(self):
        for field, value in (('commit', OTHER), ('renderer_commit', OTHER), ('channel', 'debug-ci')):
            markers = {channel: {'commit': SOURCE, 'renderer_commit': RENDERER, 'channel': channel} for channel in registry.CHANNELS}
            markers['ci'][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.run_resolve(markers)


class PublishTests(unittest.TestCase):
    def test_matching_pair_publishes_both_channels_with_markers_last(self):
        import hashlib
        import zipfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); folder = root / 'source' / 'dist'
            folder.mkdir(parents=True); variants = []
            for channel in registry.CHANNELS:
                archive = folder / f'RyGTS-{channel}-{SOURCE[:8]}.zip'
                info = {'renderer_source': f'{REPOSITORY}\n{RENDERER}\n'}
                with zipfile.ZipFile(archive, 'w') as package:
                    package.writestr(f'{archive.stem}/BUILD_INFO.txt', json.dumps(info))
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n')
                variants.append({'channel': channel, 'archive': archive.name, 'sha256': digest})
            (folder / 'CI-PACKAGES.json').write_text(json.dumps({'schema': 2, 'commit': SOURCE, 'renderer_commit': RENDERER, 'variants': variants}))
            uploads = {}; order = []
            class Response(io.BytesIO):
                status = 201
            def open_url(request, timeout):
                if isinstance(request, str): return Response(uploads[request])
                uploads[request.full_url] = request.data.read(); order.append(request.full_url)
                return Response()
            original = Path.cwd()
            try:
                os.chdir(root)
                env = {'SOURCE_SHA': SOURCE, 'RENDERER_SHA': RENDERER, 'GITHUB_REPOSITORY': 'RyGT-Team/RyGT-ci',
                       'GITHUB_RUN_ID': '123', 'GITLAB_PACKAGE_TOKEN': 'test-token', 'GITHUB_STEP_SUMMARY': str(root / 'summary')}
                with patch.dict(os.environ, env), patch.object(registry.urllib.request, 'urlopen', side_effect=open_url):
                    registry.publish()
                self.assertEqual(len(order), 6)
                self.assertTrue(all(f'development-{SOURCE}-renderer-{RENDERER}/' in url for url in order))
                self.assertTrue(all(url.endswith('/CI-BUILD.json') for url in order[-2:]))
                self.assertTrue(all(json.loads(uploads[url])['renderer_commit'] == RENDERER for url in order[-2:]))
            finally:
                os.chdir(original)

    def test_wrong_renderer_in_archive_rejected_before_network_writes(self):
        import hashlib
        import zipfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'source' / 'dist'
            folder.mkdir(parents=True)
            variants = []
            for channel in registry.CHANNELS:
                archive = folder / f'RyGTS-{channel}-{SOURCE[:8]}.zip'
                info = {'renderer_source': f'{REPOSITORY}\n{OTHER}\n'}
                with zipfile.ZipFile(archive, 'w') as package:
                    package.writestr(f'{archive.stem}/BUILD_INFO.txt', json.dumps(info))
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n')
                variants.append({'channel': channel, 'archive': archive.name, 'sha256': digest})
            (folder / 'CI-PACKAGES.json').write_text(json.dumps({'schema': 2, 'commit': SOURCE, 'variants': variants}))
            original = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, {'SOURCE_SHA': SOURCE, 'RENDERER_SHA': RENDERER}), patch.object(registry.urllib.request, 'urlopen') as request:
                    with self.assertRaises((ValueError, RuntimeError)):
                        registry.publish()
                    request.assert_not_called()
            finally:
                os.chdir(original)


if __name__ == '__main__':
    unittest.main()
