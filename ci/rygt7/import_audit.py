"""Audit static PE imports against modules present in the assembled runtime.

This does not resolve dynamically loaded libraries or validate guest GPU behavior.
"""
import struct
from pathlib import Path


def pe(path):
    data = Path(path).read_bytes()
    if data[:2] != b'MZ': raise ValueError(f'not a PE: {path}')
    u16 = lambda p: struct.unpack_from('<H', data, p)[0]
    u32 = lambda p: struct.unpack_from('<I', data, p)[0]
    header = u32(60)
    if data[header:header+4] != b'PE\0\0': raise ValueError(f'invalid PE: {path}')
    section_count = u16(header+6); opt_size = u16(header+20); opt = header+24
    magic = u16(opt); width = 8 if magic == 0x20b else 4 if magic == 0x10b else 0
    if not width: raise ValueError(f'unknown PE format: {path}')
    directories = opt + (112 if width == 8 else 96)
    sections = []
    for i in range(section_count):
        off = opt+opt_size+40*i
        vsize, va, rawsize, fileoff = struct.unpack_from('<IIII', data, off+8)
        sections.append((va, max(vsize, rawsize), fileoff))
    def pos(rva):
        for va, size, offset in sections:
            if va <= rva < va+size: return offset+rva-va
        return rva
    def text(rva):
        start=pos(rva); return data[start:data.index(b'\0', start)].decode('ascii')
    exports=set(); ordinals=set(); export_rva=u32(directories)
    if export_rva:
        at=pos(export_rva); base=u32(at+16); funcs=u32(at+20); count=u32(at+24)
        names=pos(u32(at+32)); table=pos(u32(at+28))
        exports={text(u32(names+4*i)) for i in range(count)}
        ordinals={base+i for i in range(funcs) if u32(table+4*i)}
    imports=[]; import_rva=u32(directories+8)
    if import_rva:
        at=pos(import_rva)
        while any(data[at:at+20]):
            oft, _, _, dll, ft=struct.unpack_from('<IIIII',data,at); name=text(dll); thunk=pos(oft or ft)
            while True:
                value=struct.unpack_from('<Q' if width == 8 else '<I',data,thunk)[0]
                if not value: break
                symbol=(value & 0xffff) if value >> (width*8-1) else text(value+2)
                imports.append((name,symbol)); thunk+=width
            at+=20
    return exports,ordinals,imports


def audit(runtime, converter, launcher):
    paths=list((runtime/'libs').glob('*.prx'))+list((runtime/'libs').glob('*.dll'))+list(runtime.glob('*.dll'))+[converter,launcher]
    files={}; data={}
    for path in paths:
        item=pe(path); data[path]=item; key=path.name.lower()
        if key in files and files[key].read_bytes()!=path.read_bytes(): raise ValueError(f'different local modules share name {path.name}')
        files[key]=path
    missing=[]; checked=0; external=set()
    for path, (_,_,imports) in data.items():
        for dll, symbol in imports:
            dep=files.get(dll.lower())
            if dep:
                checked+=1; names,ordinals,_=data[dep]
                if symbol not in (ordinals if isinstance(symbol,int) else names): missing.append(f'{path.name}: {dll}!{symbol}')
            elif dll.lower().endswith('.prx'): missing.append(f'{path.name}: missing {dll}')
            else: external.add(dll)
    if missing: raise ValueError('unresolved local PE imports: '+ '; '.join(missing[:30]))
    return {'checked_local_bindings':checked,'external_dlls':sorted(external),'missing':missing,
            'scope':'static PE imports; dynamically loaded modules and runtime compatibility not checked'}
