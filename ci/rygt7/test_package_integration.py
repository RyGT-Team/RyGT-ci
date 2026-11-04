"""Synthetic artifact through GT7's actual packager and the trusted ZIP validator.
Requires GT7_SOURCE_ROOT pointing to a clean, checked-out GT7 source tree.
"""
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

HERE=Path(__file__).resolve().parent

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path); obj=importlib.util.module_from_spec(spec); spec.loader.exec_module(obj);return obj

@unittest.skipUnless(os.environ.get('GT7_SOURCE_ROOT') and os.environ.get('GT7_RENDERER_ROOT'), 'GT7 source or renderer root not supplied')
class PackageIntegration(unittest.TestCase):
    def test_real_packager_and_validator(self):
        source=Path(os.environ['GT7_SOURCE_ROOT']).resolve()
        renderer_source=Path(os.environ['GT7_RENDERER_ROOT']).resolve()
        names=('GIT_CONFIG_COUNT','GIT_CONFIG_KEY_0','GIT_CONFIG_VALUE_0','GIT_CONFIG_KEY_1','GIT_CONFIG_VALUE_1','GIT_CONFIG_KEY_2','GIT_CONFIG_VALUE_2')
        original={name:os.environ.get(name) for name in names};self.addCleanup(lambda: restore_env(original))
        os.environ.update(GIT_CONFIG_COUNT='3',GIT_CONFIG_KEY_0='safe.directory',GIT_CONFIG_VALUE_0=str(source).replace('\\','/'),
                          GIT_CONFIG_KEY_1='safe.directory',GIT_CONFIG_VALUE_1=str(renderer_source).replace('\\','/'),
                          GIT_CONFIG_KEY_2='safe.directory',GIT_CONFIG_VALUE_2=str(renderer_source/'externals/vma').replace('\\','/'))
        commit=subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
        assembler=load('gt7_assembler',HERE/'assemble.py');validator=load('gt7_validator',HERE/'registry_ci.py')
        original_renderer=validator.RENDERER
        self.addCleanup(setattr, validator, 'RENDERER', original_renderer)
        validator.RENDERER=subprocess.check_output(['git','-C',str(renderer_source),'rev-parse','HEAD'],text=True).strip()
        with tempfile.TemporaryDirectory(prefix='gt7-ci-package-test-') as tmp:
            root=Path(tmp)
            for n in ('built','renderer/externals/fixture','stubs','ucrt/bin','deps/fixture','out'):
                (root/n).mkdir(parents=True)
            (root/'renderer/LICENSE').write_text('renderer license')
            (root/'renderer/externals/fixture/LICENSE').write_text('dependency license')
            (root/'deps/fixture/LICENSE').write_text('dependency license')
            nested=root/'deps/fixture/LICENSES/nested';nested.mkdir(parents=True)
            (nested/'NOTICE.txt').write_text('nested dependency notice')
            nested_renderer=root/'renderer/externals/fixture/LICENSES/nested';nested_renderer.mkdir(parents=True)
            (nested_renderer/'NOTICE.txt').write_text('nested renderer notice')
            for n in ('libgcc','libstdc++','libwinpthread'):
                p=root/'ucrt/share/licenses'/n;p.mkdir(parents=True);(p/'LICENSE').write_text('runtime license')
            names=[x.strip() for x in (source/'build-support/runtime-files.txt').read_text().splitlines() if x.strip() and not x.startswith('#')]
            base=root/'base.zip'
            with zipfile.ZipFile(base,'w') as z:
                z.writestr('base/manifest.json','{}')
                for n in names:z.writestr('base/libs/'+n,b'base fixture')
                for n in ('amd_fidelityfx_vk.dll','amd_fidelityfx_vk-LICENSE.txt'):z.writestr('base/'+n,b'fsr fixture')
                z.writestr('base/user/save.bin',b'private fixture')
            assembler.BASE_SHA256=validator.BASE=assembler.sha(base)
            for n in assembler.REQUIRED_RUNTIME:(root/'built'/n).write_bytes(b'runtime fixture')
            for n in assembler.RENDERER_LIBS:(root/'renderer'/n).write_bytes(b'renderer fixture')
            for n in assembler.STUB_LIBS:(root/'stubs'/n).write_bytes(b'stub fixture')
            for n in assembler.GCC_DLLS:(root/'ucrt/bin'/n).write_bytes(b'ucrt fixture')
            for n in ('launcher.exe','converter.exe','probe.exe'):(root/n).write_bytes(b'fixture')
            args=SimpleNamespace(source=source,base=base,runtime=root/'assembled',built_libs=root/'built',
              renderer_build=root/'renderer',stubs=root/'stubs',ucrt=root/'ucrt',info=root/'BUILD_INFO.json',
              source_sha=commit,renderer_sha=validator.RENDERER,renderer_configured_url=validator.CONFIGURED,
              renderer_fetch_url=validator.FETCHED,launcher=root/'launcher.exe',converter=root/'converter.exe',
              wheel_probe=root/'probe.exe',renderer_source=renderer_source,renderer_deps_source=root/'renderer/externals',deps_source=root/'deps',out=root/'out',
              name='RyGT7-ci-'+commit[:8])
            assembler.assemble(args)
            info=json.loads(args.info.read_text());info['renderer_vma_patch']=assembler.verify_renderer(renderer_source)
            args.info.write_text(json.dumps(info),encoding='utf-8')
            assembler.package(args);validator.validate(root/'out',commit)
            archive=root/'out'/(args.name+'.zip')
            with zipfile.ZipFile(archive) as z:
                self.assertIn(args.name+'/licenses/RyGT7/3rdparty/fixture/LICENSES/nested/NOTICE.txt',z.namelist())
                self.assertIn(args.name+'/licenses/shadGT7/externals/fixture/LICENSES/nested/NOTICE.txt',z.namelist())

def restore_env(original):
    for name,val in original.items():
        if val is None:os.environ.pop(name,None)
        else:os.environ[name]=val

if __name__=='__main__':unittest.main()
