"""Trusted GitHub workflow control: resolve GitLab development and publish CI packages."""
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request
import zipfile

API = 'https://gitlab.com/api/v4/projects/rygt-team%2FRyGTS'
CHANNELS = ('ci', 'debug-ci')


def version(commit, renderer):
    if not re.fullmatch('[0-9a-f]{40}', commit) or not re.fullmatch('[0-9a-f]{40}', renderer):
        raise ValueError('Invalid source or renderer commit')
    return 'development-' + commit + '-renderer-' + renderer


def renderer_revision(commit):
    with urllib.request.urlopen(API + '/repository/files/renderer%2Fsource.json/raw?ref=' + commit, timeout=60) as response:
        policy = json.load(response)
    if (not isinstance(policy, dict) or not isinstance(policy.get('repository'), str) or
            policy['repository'].lower() != 'https://gitlab.com/rygt-team/shadgt.git' or
            policy.get('branch') != 'ryty-gpu'):
        raise ValueError('Unexpected renderer repository or branch')
    revision = policy.get('commit', '')
    if revision == 'HEAD':
        url = 'https://gitlab.com/api/v4/projects/rygt-team%2FShadGT/repository/branches/ryty-gpu'
        with urllib.request.urlopen(url, timeout=60) as response:
            revision = json.load(response)['commit']['id']
    if not isinstance(revision, str) or not re.fullmatch('[0-9a-f]{40}', revision):
        raise ValueError('Invalid renderer revision')
    return revision


def resolve():
    with urllib.request.urlopen(API + '/repository/branches/development', timeout=60) as response:
        commit = json.load(response)['commit']['id']
    if not isinstance(commit, str) or not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Invalid source commit')
    renderer = renderer_revision(commit)
    package_version = version(commit, renderer)
    needed = 'false'
    for channel in CHANNELS:
        url = API + '/packages/generic/' + channel + '/' + package_version + '/CI-BUILD.json'
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                previous = json.load(response)
            if (previous.get('commit') != commit or previous.get('channel') != channel or
                    previous.get('renderer_commit') != renderer):
                raise RuntimeError('Existing package marker has unexpected identity')
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            needed = 'true'
    if os.environ.get('FORCE_BUILD') == 'true':
        needed = 'true'
    with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
        output.write(f'commit={commit}\nrenderer_commit={renderer}\nversion={package_version}\nneeded={needed}\n')
    print(f'GitLab development: {commit}; renderer: {renderer}; build needed: {needed}')


def publish():
    commit = os.environ['SOURCE_SHA']
    renderer = os.environ['RENDERER_SHA']
    package_version = version(commit, renderer)
    folder = Path('source/dist')
    manifest = json.loads((folder / 'CI-PACKAGES.json').read_text())
    variants = manifest.get('variants', [])
    if (manifest.get('schema') != 2 or manifest.get('commit') != commit or
            [v.get('channel') for v in variants] != list(CHANNELS) or
            manifest.get('renderer_commit', renderer) != renderer):
        raise RuntimeError('Both package channels are required')
    packages = []
    # Validate every package before making any network writes.
    for variant in variants:
        channel = variant['channel']
        archive = folder / f'RyGTS-{channel}-{commit[:8]}.zip'
        checksum = archive.with_suffix('.zip.sha256')
        digest, name = checksum.read_text().strip().split('  ', 1)
        with archive.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if (name != archive.name or actual != digest or variant.get('archive') != name or
                variant.get('sha256') != digest):
            raise RuntimeError('Final package checksum mismatch')
        with zipfile.ZipFile(archive) as package:
            build = json.loads(package.read(archive.stem + '/BUILD_INFO.txt'))
        source = build.get('renderer_source', '').splitlines()
        if (len(source) != 2 or source[0].lower() != 'https://gitlab.com/rygt-team/shadgt.git' or
                source[1] != renderer):
            raise RuntimeError('Package renderer differs from resolved CI revision')
        packages.append((channel, archive, checksum, digest))
    run = f'https://github.com/{os.environ["GITHUB_REPOSITORY"]}/actions/runs/{os.environ["GITHUB_RUN_ID"]}'
    uploads = []
    markers = []
    for channel, archive, checksum, digest in packages:
        marker = folder / channel / 'CI-BUILD.json'
        marker.parent.mkdir(exist_ok=True)
        marker.write_text(json.dumps({'type': 'CI build', 'branch': 'development', 'commit': commit,
                                     'renderer_commit': renderer,
                                     'channel': channel, 'build_url': run, 'filename': archive.name,
                                     'sha256': digest}, indent=2) + '\n')
        uploads.extend(((channel, archive), (channel, checksum)))
        markers.append((channel, marker))
    token = os.environ['GITLAB_PACKAGE_TOKEN']
    # Marker is uploaded last: scheduled checks only skip fully published packages.
    for channel, file in uploads + markers:
        url = API + '/packages/generic/' + channel + '/' + package_version + '/' + file.name
        with file.open('rb') as stream:
            request = urllib.request.Request(url, data=stream, method='PUT', headers={
                'DEPLOY-TOKEN': token, 'Content-Length': str(file.stat().st_size),
                'Content-Type': 'application/octet-stream'})
            with urllib.request.urlopen(request, timeout=600) as response:
                if response.status != 201:
                    raise RuntimeError('Package upload did not return Created')
        # Verify server content before publishing the completion marker.
        with urllib.request.urlopen(url, timeout=600) as response:
            remote_digest = hashlib.file_digest(response, 'sha256').hexdigest()
        with file.open('rb') as stream:
            local_digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if remote_digest != local_digest:
            raise RuntimeError('Uploaded package checksum mismatch')
        print('Published and verified:', url)
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as output:
        output.write(f'CI build `{commit}`, renderer `{renderer}`\n\n')
        for channel, archive, checksum, _ in packages:
            output.write(f'{channel}: [ZIP]({API}/packages/generic/{channel}/{package_version}/{archive.name})'
                         f' | [SHA-256]({API}/packages/generic/{channel}/{package_version}/{checksum.name})\n\n')


if __name__ == '__main__':
    import sys
    {'resolve': resolve, 'publish': publish}[sys.argv[1]]()
