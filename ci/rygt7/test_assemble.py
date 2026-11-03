import importlib.util
import json
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('assemble', HERE / 'assemble.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)

class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for x in ('source/build-support', 'built', 'renderer', 'stubs', 'ucrt/bin', 'out'):
            (self.root / x).mkdir(parents=True)
        self.names = list(module.RENDERER_LIBS) + list(module.STUB_LIBS) + list(module.GCC_DLLS) + list(module.REQUIRED_RUNTIME) + ['old.prx']
        (self.root/'source/build-support/runtime-files.txt').write_text('\n'.join(self.names))
        self.base = self.root/'base.zip'
        with zipfile.ZipFile(self.base, 'w') as z:
            z.writestr('base/manifest.json', '{}')
            for n in self.names: z.writestr('base/libs/'+n, b'base')
            for n in ('amd_fidelityfx_vk.dll','amd_fidelityfx_vk-LICENSE.txt'): z.writestr('base/'+n, b'fsr')
            z.writestr('base/user/save.bin', b'private')
        self.old_hash = module.BASE_SHA256; module.BASE_SHA256 = module.sha(self.base)
        self.addCleanup(setattr, module, 'BASE_SHA256', self.old_hash)
        for n in module.RENDERER_LIBS: (self.root/'renderer'/n).write_bytes(b'renderer')
        for n in module.STUB_LIBS: (self.root/'stubs'/n).write_bytes(b'stub')
        for n in module.GCC_DLLS: (self.root/'ucrt/bin'/n).write_bytes(b'ucrt')
        for n in module.REQUIRED_RUNTIME: (self.root/'built'/n).write_bytes(b'fresh runtime')
        (self.root/'built/old.prx').write_bytes(b'fresh')
        for n in ('launcher.exe','converter.exe','probe.exe'): (self.root/n).write_bytes(n.encode())
        self.args = SimpleNamespace(source=self.root/'source',base=self.base,runtime=self.root/'assembled',
          built_libs=self.root/'built',renderer_build=self.root/'renderer',stubs=self.root/'stubs',ucrt=self.root/'ucrt',
          info=self.root/'BUILD_INFO.json',source_sha='a'*40,renderer_sha='b'*40,renderer_configured_url='configured',
          renderer_fetch_url='mirror', launcher=self.root/'launcher.exe',converter=self.root/'converter.exe',
          wheel_probe=self.root/'probe.exe',renderer_source=self.root/'renderer',out=self.root/'out',name='RyGT7-ci-aaaaaaaa')

    def test_allowlist_overlay_and_private_exclusion(self):
        module.assemble(self.args)
        info=json.loads(self.args.info.read_text())
        self.assertEqual(info['runtime_files']['libs/old.prx']['origin'],'rygt7-source-build')
        self.assertEqual(info['runtime_files']['libs/libSceVideoOut.prx']['origin'],'shadgt7-source-build')
        self.assertEqual(info['runtime_files']['libs/libSceMbus.prx']['origin'],'rygt7-gt7-stub-build')
        self.assertEqual(info['runtime_files']['libs/libstdc++-6.dll']['origin'],'ucrt64-toolchain')
        self.assertFalse((self.args.runtime/'user').exists())
        self.assertEqual((self.args.runtime/'libs/old.prx').read_bytes(),b'fresh')

    def test_hash_mismatch_and_missing_renderer_fail(self):
        module.BASE_SHA256='0'*64
        with self.assertRaisesRegex(ValueError,'SHA256'): module.assemble(self.args)
        module.BASE_SHA256=module.sha(self.base)
        (self.root/'renderer'/module.RENDERER_LIBS[0]).unlink()
        with self.assertRaisesRegex(ValueError,'expected one'): module.assemble(self.args)

    def test_required_fresh_runtime_cannot_fall_back_to_base(self):
        (self.root/'built'/module.REQUIRED_RUNTIME[0]).unlink()
        with self.assertRaisesRegex(ValueError,'required fresh runtime output missing'):
            module.assemble(self.args)

    def test_invalid_allowlist_rejected(self):
        (self.root/'source/build-support/runtime-files.txt').write_text('../escape.prx\n')
        with self.assertRaisesRegex(ValueError,'allowlist'): module.assemble(self.args)


class ImportAuditTests(unittest.TestCase):
    def test_local_named_ordinal_and_missing_prx(self):
        import import_audit
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'libs').mkdir(); provider=root/'libs/provider.prx'; consumer=root/'libs/consumer.prx'
            exe=root/'launcher.exe'; converter=root/'converter.exe'
            for path in (provider,consumer,exe,converter):path.write_bytes(path.name.encode())
            def parse(path):
                if path==provider:return {'entry'},{42},[]
                if path==consumer:return set(),set(),[('provider.prx','entry'),('provider.prx',42),('kernel32.dll','x')]
                return set(),set(),[]
            with patch.object(import_audit,'pe',side_effect=parse):
                result=import_audit.audit(root,converter,exe)
                self.assertEqual(result['checked_local_bindings'],2)
                self.assertEqual(result['external_dlls'],['kernel32.dll'])
                def missing(path):
                    if path==consumer:return set(),set(),[('missing.prx','entry')]
                    return parse(path)
                with patch.object(import_audit,'pe',side_effect=missing):
                    with self.assertRaisesRegex(ValueError,'missing.prx'):import_audit.audit(root,converter,exe)

class RendererProvenanceTests(unittest.TestCase):
    def test_exact_patch_only(self):
        import renderer_provenance as provenance
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); patch_file=root/provenance.PATCH_PATH; target=root/'externals/vma'/provenance.TARGET
            patch_file.parent.mkdir(parents=True);target.parent.mkdir(parents=True)
            patch_file.write_bytes(b'--- a/include/vk_mem_alloc.h\n+++ b/include/vk_mem_alloc.h\n@@ -1 +1 @@\n-old\n+new\n')
            target.write_bytes(b'new\n')
            def fake_git(repo,*args):
                if args[0]=='show':return b'old\n'
                return b' m externals/vma\n' if Path(repo)==root else b' M include/vk_mem_alloc.h\n'
            with patch.object(provenance,'PATCH_SHA',module.sha(patch_file)),patch.object(provenance,'git',side_effect=fake_git):
                self.assertTrue(provenance.verify(root)['verified'])
                target.write_bytes(b'new\nextra\n')
                with self.assertRaisesRegex(ValueError,'VMA'):provenance.verify(root)
                patch_file.write_bytes(b'tampered')
                with self.assertRaisesRegex(ValueError,'unexpected VMA patch'):provenance.verify(root)

if __name__ == '__main__': unittest.main()
