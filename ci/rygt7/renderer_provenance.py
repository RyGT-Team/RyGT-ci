"""Verify the pinned renderer is clean except for its exact expected VMA patch."""
import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

PATCH_PATH = 'externals/vma-patches/0001-trim-empty-default-blocks.patch'
PATCH_SHA = '989c12d4729935f11c05a13eeab0fc96fc99e53055c97bd0b81bce04448d6180'
TARGET = 'include/vk_mem_alloc.h'

def git(repo, *args):
    return subprocess.check_output(['git','-C',str(repo),*args])

def verify(renderer):
    renderer=Path(renderer);patch=renderer/PATCH_PATH;vma=renderer/'externals/vma'
    if hashlib.sha256(patch.read_bytes()).hexdigest() != PATCH_SHA: raise ValueError('unexpected VMA patch')
    root_status=git(renderer,'status','--porcelain','--untracked-files=all').decode().splitlines()
    if any(line[3:] != 'externals/vma' or line[:2] not in (' M',' m') for line in root_status):
        raise ValueError('unexpected renderer tracked changes')
    status=git(vma,'status','--porcelain','--untracked-files=all').decode().splitlines()
    if status != [' M ' + TARGET]: raise ValueError('unexpected VMA tracked changes')
    original=git(vma,'show','HEAD:'+TARGET)
    with tempfile.TemporaryDirectory(prefix='gt7-vma-verify-') as temp:
        copy=Path(temp)/TARGET;copy.parent.mkdir(parents=True);shutil.copyfile(vma/TARGET,copy)
        result=subprocess.run(['git','apply','--reverse','--no-index',str(patch.resolve())],cwd=temp,
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        if result.returncode: raise ValueError('VMA patch cannot be reversed exactly')
        # Git checkout may translate text line endings on Windows; compare normalized text bytes.
        if copy.read_bytes().replace(b'\r\n',b'\n') != original.replace(b'\r\n',b'\n'):
            raise ValueError('VMA has changes beyond pinned patch')
    return {'path':PATCH_PATH,'sha256':PATCH_SHA,'target':TARGET,'verified':True}
