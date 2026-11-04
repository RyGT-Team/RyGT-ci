"""Assemble only the GT7 package allowlist, then call its own packager."""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import zipfile
from import_audit import audit
from renderer_provenance import verify as verify_renderer
from pathlib import Path, PurePosixPath

BASE_URL = 'https://gitlab.com/api/v4/projects/88084032/packages/generic/rygt7/2026-10-31/RyGT7-2026-10-31-prerelease.zip'
BASE_SHA256 = 'd8efb3c45b9eb164a66330d60e68638ff3d2a2a74fd51680482e989791697d2e'
GCC_DLLS = ('libgcc_s_seh-1.dll', 'libstdc++-6.dll', 'libwinpthread-1.dll')
RENDERER_LIBS = ('libSceVideoOut.prx', 'libSceAudio3d.prx', 'libSceAjm.prx',
                 'libSceNgs2.prx', 'libSceZlib.prx', 'libSceDiscMap.prx',
                 'libSceScreenShot.prx', 'libSceNpAuth.prx')
STUB_LIBS = ('libSceTextToSpeech.prx', 'libSceMbus.prx')
REQUIRED_RUNTIME = ('libc.prx', 'libkernel.prx', 'libSceFiber.prx', 'libScePad.prx',
                    'libSceHttp.prx', 'libSceNet.prx', 'libSceNetCtl.prx', 'libSceNpManager.prx',
                    'libSceNpSessionSignaling.prx', 'libSceRtc.prx', 'libSceSsl.prx')

def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def one_file(folder, name):
    found = [p for p in Path(folder).rglob('*') if p.is_file() and p.name.lower() == name.lower()]
    if len(found) != 1:
        raise ValueError(f'expected one {name} under {folder}, found {len(found)}')
    return found[0]

def assemble(args):
    if sha(args.base) != BASE_SHA256:
        raise ValueError('runtime base archive SHA256 mismatch')
    names = [n.strip() for n in (args.source / 'build-support/runtime-files.txt').read_text().splitlines()
             if n.strip() and not n.startswith('#')]
    if len({n.lower() for n in names}) != len(names) or any(Path(n).name != n or '/' in n or '\\' in n for n in names):
        raise ValueError('invalid or duplicate allowlist entry')
    args.runtime.mkdir(parents=True, exist_ok=False)
    libs = args.runtime / 'libs'; libs.mkdir()
    origins = {}
    with zipfile.ZipFile(args.base) as z:
        manifests = [x for x in z.namelist() if x.endswith('/manifest.json') and len(PurePosixPath(x).parts) == 2]
        if len(manifests) != 1:
            raise ValueError('expected exactly one base manifest')
        prefix = PurePosixPath(manifests[0]).parts[0]
        def take(relative, target):
            expected = f'{prefix}/{relative}'
            matches = [x for x in z.namelist() if x.lower() == expected.lower()]
            if len(matches) != 1 or z.getinfo(matches[0]).is_dir():
                raise ValueError(f'expected exactly one base entry: {expected}')
            with z.open(matches[0]) as src, target.open('wb') as dst:
                shutil.copyfileobj(src, dst)
        for name in names:
            take('libs/' + name, libs / name)
            origins['libs/' + name] = {'origin': 'pinned-base'}
        for name in ('amd_fidelityfx_vk.dll', 'amd_fidelityfx_vk-LICENSE.txt'):
            take(name, args.runtime / name); origins[name] = {'origin': 'pinned-base'}
    allow = {n.lower(): n for n in names}
    # A clean build directory is required; only exact allowlisted names are considered.
    for item in args.built_libs.iterdir():
        if item.is_file() and item.name.lower() in allow:
            name = allow[item.name.lower()]; shutil.copyfile(item, libs / name)
            origins['libs/' + name] = {'origin': 'rygt7-source-build'}
    for name in REQUIRED_RUNTIME:
        if name.lower() not in allow or origins['libs/' + allow[name.lower()]]['origin'] != 'rygt7-source-build':
            raise ValueError(f'required fresh runtime output missing: {name}')
    for name in RENDERER_LIBS:
        if name.lower() not in allow: raise ValueError(f'renderer output absent from allowlist: {name}')
        shutil.copyfile(one_file(args.renderer_build, name), libs / allow[name.lower()])
        origins['libs/' + allow[name.lower()]] = {'origin': 'shadgt7-source-build'}
    for name in STUB_LIBS:
        if name.lower() not in allow: raise ValueError(f'stub absent from allowlist: {name}')
        shutil.copyfile(one_file(args.stubs, name), libs / allow[name.lower()])
        origins['libs/' + allow[name.lower()]] = {'origin': 'rygt7-gt7-stub-build'}
    for name in GCC_DLLS:
        source = args.ucrt / 'bin' / name
        shutil.copyfile(source, args.runtime / name); origins[name] = {'origin': 'ucrt64-toolchain'}
        if name.lower() in allow:
            shutil.copyfile(source, libs / allow[name.lower()]); origins['libs/' + allow[name.lower()]] = {'origin': 'ucrt64-toolchain'}
    for name, metadata in origins.items():
        metadata['sha256'] = sha(args.runtime / name)
    info = {'schema': 1, 'rygt7_commit': args.source_sha, 'renderer_commit': args.renderer_sha,
            'renderer_configured_url': args.renderer_configured_url, 'renderer_fetch_url': args.renderer_fetch_url,
            'base_url': BASE_URL, 'base_sha256': BASE_SHA256, 'runtime_files': origins}
    control = {'repository': os.environ.get('GITHUB_REPOSITORY'), 'commit': os.environ.get('GITHUB_SHA'),
               'attempt': os.environ.get('GITHUB_RUN_ATTEMPT'),
               'run_url': (f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
                           if os.environ.get('GITHUB_REPOSITORY') and os.environ.get('GITHUB_RUN_ID') else None)}
    info['ci_control'] = control
    if getattr(args, 'toolchain_info', None):
        info['toolchain'] = json.loads(args.toolchain_info.read_text(encoding='utf-8-sig'))
    info['components'] = {name: {'sha256': sha(path), 'origin': origin} for name, path, origin in (
        ('launcher', args.launcher, 'rygt7-source-build'), ('converter', args.converter, 'rygt7-source-build'),
        ('wheel_probe', args.wheel_probe, 'rygt7-source-build'))}
    args.info.write_text(json.dumps(info, indent=2) + '\n', encoding='utf-8')

def package(args):
    spec = importlib.util.spec_from_file_location('gt7_packager', args.source / 'build-support/package_release.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.PROVENANCE = 'Per-file provenance and hashes are recorded in BUILD_INFO.json; this CI package uses a pinned GT7 runtime base and fresh source-built overlays.'
    sys.argv = ['package_release.py', '--runtime', str(args.runtime), '--launcher', str(args.launcher),
                '--converter', str(args.converter), '--renderer-source', str(args.renderer_source),
                '--renderer-deps-source', str(getattr(args, 'renderer_deps_source', args.renderer_source / 'externals')),
                '--wheel-probe', str(args.wheel_probe), '--gcc-licenses', str(args.ucrt / 'share/licenses'),
                '--deps-source', str(getattr(args, 'deps_source', args.source / '3rdparty')),
                '--out', str(args.out), '--name', args.name]
    module.main()
    folder = args.out / args.name
    shutil.copyfile(args.info, folder / 'BUILD_INFO.json')
    # The release template describes historical gameplay, not this CI build.
    readme = folder / 'README.txt'
    readme.write_text('RyGT7 source CI build. No game, firmware or player data is included.\n'
                      'This CI artifact has not been validated in game.\n'
                      'See BUILD_INFO.json for source and runtime-base provenance and docs/launcher.md for setup.\n', encoding='utf-8')
    manifest_path = folder / 'manifest.json'; manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    for record in manifest['files']:
        if record['path'] == 'README.txt':
            record['bytes'] = readme.stat().st_size; record['sha256'] = sha(readme)
            break
    else: raise ValueError('packager omitted README.txt')
    info = folder / 'BUILD_INFO.json'
    manifest['files'].append({'path': 'BUILD_INFO.json', 'bytes': info.stat().st_size, 'sha256': sha(info)})
    manifest['files'].sort(key=lambda row: row['path'])
    manifest_path.write_text(json.dumps(manifest, indent=1), encoding='utf-8')
    archive = args.out / (args.name + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for file in sorted(folder.rglob('*')):
            if file.is_file(): z.write(file, (Path(args.name) / file.relative_to(folder)).as_posix())
    digest = sha(archive); archive.with_suffix('.zip.sha256').write_text(f'{digest}  {archive.name}\n', encoding='ascii')
    marker = {'schema': 1, 'project': 'rygt-team/rygt7', 'branch': 'main', 'commit': args.source_sha,
              'renderer': {'configured_repository': args.renderer_configured_url,
                           'fetched_repository': args.renderer_fetch_url, 'commit': args.renderer_sha},
              'runtime_base': {'url': BASE_URL, 'sha256': BASE_SHA256},
              'archive': {'name': archive.name, 'sha256': digest}, 'build_info': 'BUILD_INFO.json',
              'ci_control': json.loads(args.info.read_text(encoding='utf-8'))['ci_control']}
    (args.out / 'CI-BUILD.json').write_text(json.dumps(marker, indent=2) + '\n', encoding='utf-8')
    print(f'{archive} SHA256 {digest}')

def main():
    p = argparse.ArgumentParser()
    for name in ('source','base','runtime','built-libs','renderer-build','stubs','ucrt','info','launcher','converter','renderer-source','wheel-probe','out'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--toolchain-info', type=Path)
    for name in ('source-sha','renderer-sha','renderer-configured-url','renderer-fetch-url','name'):
        p.add_argument('--' + name, required=True)
    args = p.parse_args()
    if not re.fullmatch(r'[0-9a-f]{40}', args.source_sha): raise ValueError('invalid source SHA')
    if not re.fullmatch(r'[0-9a-f]{40}', args.renderer_sha) or args.renderer_sha != os.environ.get('RENDERER_SHA'):
        raise ValueError('renderer SHA differs from resolved CI revision')
    if args.name != 'RyGT7-ci-' + args.source_sha[:8]: raise ValueError('unexpected package name')
    assemble(args)
    info = json.loads(args.info.read_text(encoding="utf-8"))
    info["static_pe_import_audit"] = audit(args.runtime, args.converter, args.launcher)
    info["renderer_vma_patch"] = verify_renderer(args.renderer_source)
    args.info.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    package(args)

if __name__ == '__main__': main()
