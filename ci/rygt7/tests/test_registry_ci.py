import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import io
import urllib.error
import zipfile

MODULE = Path(__file__).resolve().parents[1] / 'registry_ci.py'
spec = importlib.util.spec_from_file_location('registry_ci', MODULE)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
COMMIT = 'a' * 40

class Validation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.folder = Path(self.tmp.name)
        self.files = {name: b'fixture' for name in m.REQUIRED if name != 'BUILD_INFO.json'}
        for n in ('libc.prx','libkernel.prx','libScePad.prx'): self.files['libs/'+n]=b'fixture'
        self.info = {'schema': 1, 'rygt7_commit': COMMIT, 'renderer_commit': m.RENDERER,
                     'renderer_configured_url': m.CONFIGURED, 'renderer_fetch_url': m.FETCHED,
                     'base_url': m.BASE_URL, 'base_sha256': m.BASE, 'renderer_vma_patch': {'path':'externals/vma-patches/0001-trim-empty-default-blocks.patch','sha256':'989c12d4729935f11c05a13eeab0fc96fc99e53055c97bd0b81bce04448d6180','target':'include/vk_mem_alloc.h','verified':True}, 'ci_control': {'repository':None,'commit':None,'run_url':None,'attempt':None},
                     'runtime_files': {p: {'origin': ('shadgt7-source-build' if p.endswith('libSceVideoOut.prx') else 'rygt7-source-build'), 'sha256': m.digest(b'fixture')} for p in self.files if p.startswith('libs/')}}
    def tearDown(self): self.tmp.cleanup()
    def write(self, extra=(), info=None, bad_hash=False):
        files = dict(self.files); files['BUILD_INFO.json'] = json.dumps(info or self.info).encode()
        records = [{'path': p, 'bytes': len(b), 'sha256': m.digest(b)} for p,b in files.items()]
        if bad_hash: records[0]['sha256'] = '0'*64
        manifest = {'sources': {'RyGT7': {'commit': COMMIT, 'uncommitted_changes': False}, 'shadGT7': {'commit':m.RENDERER, 'uncommitted_changes':False}}, 'files': records}
        name = f'RyGT7-ci-{COMMIT[:8]}.zip'; top = name[:-4]
        with zipfile.ZipFile(self.folder / name, 'w') as z:
            for p,b in files.items(): z.writestr(top+'/'+p,b)
            z.writestr(top+'/manifest.json', json.dumps(manifest))
            for p in extra: z.writestr(top+'/'+p,b'x')
        checksum = m.digest((self.folder/name).read_bytes())
        (self.folder/(name+'.sha256')).write_text(checksum+'  '+name+'\n')
        marker = {'schema':1, 'project':'rygt-team/rygt7', 'branch':'main', 'commit':COMMIT,
                  'renderer': {'configured_repository':m.CONFIGURED, 'fetched_repository':m.FETCHED, 'commit':m.RENDERER},
                  'runtime_base': {'url':m.BASE_URL, 'sha256':m.BASE}, 'ci_control': {'repository':None,'commit':None,'run_url':None,'attempt':None}, 'archive':{'name':name,'sha256':checksum}, 'build_info':'BUILD_INFO.json'}
        (self.folder/'CI-BUILD.json').write_text(json.dumps(marker))
    def test_valid(self):
        self.write(); m.validate(self.folder, COMMIT)
    def test_missing_components(self):
        for path in ('RyGT7.exe','converter/ryty.exe','libs/libSceVideoOut.prx'):
            value=self.files.pop(path); self.write()
            with self.assertRaises(ValueError): m.validate(self.folder, COMMIT)
            self.files[path]=value
    def test_unsafe_and_duplicate(self):
        for path in ('../escape', 'GAME/eboot.bin', 'rygt7.EXE', 'docs/app0/file', 'libs/sce_module/file', 'user/file'):
            self.write(extra=[path])
            with self.assertRaises(ValueError): m.validate(self.folder, COMMIT)
    def test_provenance_and_hash(self):
        for field in ('rygt7_commit','renderer_commit','base_sha256'):
            info=dict(self.info); info[field]='0'*40; self.write(info=info)
            with self.assertRaises(ValueError): m.validate(self.folder, COMMIT)
        self.write(bad_hash=True)
        with self.assertRaises(ValueError): m.validate(self.folder, COMMIT)
    def test_mismatched_source_and_checksum(self):
        self.write()
        with self.assertRaises(ValueError): m.validate(self.folder,'b'*40)
        checksum=next(self.folder.glob('*.sha256')); checksum.write_text('invalid')
        with self.assertRaises(ValueError): m.validate(self.folder,COMMIT)

    def test_runtime_coverage_and_origin(self):
        for mode in ('missing', 'wrong_origin', 'extra'):
            info=dict(self.info); info['runtime_files']={k:dict(v) for k,v in self.info['runtime_files'].items()}
            if mode == 'missing': info['runtime_files'].pop('libs/libSceFiber.prx')
            elif mode == 'wrong_origin': info['runtime_files']['libs/libSceFiber.prx']['origin']='pinned-base'
            else: info['runtime_files']['not-present']={'origin':'pinned-base','sha256':'0'*64}
            self.write(info=info)
            with self.assertRaises(ValueError): m.validate(self.folder,COMMIT)
    def test_no_secret_in_output(self):
        self.write()
        output=io.StringIO()
        with mock.patch('sys.stdout',output): m.validate(self.folder,COMMIT)
        self.assertNotIn('secret-value',output.getvalue())
    def test_publish_order_and_readback_failure(self):
        self.write(); written=[]; bodies={}
        class Response(io.BytesIO):
            status=201
        def open_url(request, timeout=0):
            if isinstance(request, str):
                return Response(bodies[request])
            data=request.data.read(); written.append(request.full_url.rsplit('/',1)[-1]); bodies[request.full_url]=data
            return Response(b'')
        with mock.patch.dict(os.environ, {'GITLAB_PACKAGE_TOKEN':'secret-value'}), mock.patch.object(m.urllib.request,'urlopen',side_effect=open_url):
            m.publish(self.folder,COMMIT)
        self.assertEqual(written[-1], 'CI-BUILD.json')
        self.assertEqual(written[0], 'CI-PENDING.json')
        self.assertEqual(len(written),4)
        written.clear()
        def broken(request,timeout=0):
            if isinstance(request,str): return Response(b'wrong')
            written.append(request.full_url); return Response()
        with mock.patch.dict(os.environ, {'GITLAB_PACKAGE_TOKEN':'secret-value'}), mock.patch.object(m.urllib.request,'urlopen',side_effect=broken):
            with self.assertRaises(ValueError): m.publish(self.folder,COMMIT)
        self.assertEqual(len(written),1)
    def test_interrupted_forced_retry_requires_rebuild(self):
        self.write()
        old=json.loads((self.folder/'CI-BUILD.json').read_text())
        pending=json.loads(json.dumps(old)); pending['ci_control']['attempt']='2'
        class Response(io.BytesIO): pass
        output=self.folder/'output'
        calls=[Response(json.dumps({'commit':{'id':COMMIT}}).encode()),
               Response(json.dumps(old).encode()), Response(json.dumps(pending).encode())]
        with mock.patch.dict(os.environ, {'GITHUB_OUTPUT':str(output),'FORCE_BUILD':'false'}), mock.patch.object(m.urllib.request,'urlopen',side_effect=calls): m.resolve()
        self.assertIn('needed=true',output.read_text())
        calls=[Response(json.dumps({'commit':{'id':COMMIT}}).encode()),
               Response(json.dumps(old).encode()), Response(json.dumps(old).encode())]
        output.write_text('')
        with mock.patch.dict(os.environ, {'GITHUB_OUTPUT':str(output),'FORCE_BUILD':'false'}), mock.patch.object(m.urllib.request,'urlopen',side_effect=calls): m.resolve()
        self.assertIn('needed=false',output.read_text())
    def test_resolve_missing_and_denied(self):
        class Response(io.BytesIO): pass
        url='https://example.invalid'
        for code in (404,401):
            calls=[Response(json.dumps({'commit':{'id':COMMIT}}).encode()), urllib.error.HTTPError(url,code,'error',{},None)]
            output=self.folder/'output'
            with mock.patch.dict(os.environ, {'GITHUB_OUTPUT':str(output),'FORCE_BUILD':'false'}), mock.patch.object(m.urllib.request,'urlopen',side_effect=calls):
                if code == 404: m.resolve(); self.assertIn('needed=true',output.read_text())
                else:
                    with self.assertRaises(urllib.error.HTTPError): m.resolve()
    def test_resolve_rejects_mismatched_marker(self):
        class Response(io.BytesIO): pass
        calls=[Response(json.dumps({'commit':{'id':COMMIT}}).encode()),Response(json.dumps({'commit':'b'*40}).encode())]
        with mock.patch.dict(os.environ, {'GITHUB_OUTPUT':str(self.folder/'output')}), mock.patch.object(m.urllib.request,'urlopen',side_effect=calls):
            with self.assertRaises(ValueError): m.resolve()

if __name__ == '__main__': unittest.main()







