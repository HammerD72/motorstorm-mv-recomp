#!/usr/bin/env python3
"""Extract MS1's symtab-named embedded SPU inputs from a user-supplied debug EBOOT.

The plain SPU ELF scan (vendor/ps3recomp/tools/extract_spu_images.py) only
finds images 1-2. The debug EBOOT also embeds, named by its .symtab:

  * ten WWS C0DEC0DE job binaries (stable ids 210-219):
      _binary_<stem>_wwsjob_image_start/_size, plus the two STT_OBJECT blobs
      mp3StereoJobCode / mp3SurroundJobCode;
  * the jobmanager policy-module template (_binary_jobmanager_file_start),
    registered as id 3 after appending 16 zero bytes and wrapping it as an
    SPU ELF at base/entry 0xA00;
  * the 672-byte jobbootstrap blob, which has no registration and is emitted
    for completeness only.

Every image is mapped through its containing PT_LOAD (file = p_offset + EA -
p_vaddr), checked against the committed identities in jobmod_meta.json and
written to an ignored output tree. Section sh_size includes alignment padding
and is never used as an image size. Any identity mismatch, missing symbol or
unexpected header exits non-zero: a silently different module would register
under a fingerprint the game never stages.

Outputs (under --out):
  jobmods/jobmod_<sha256-12>_e<entryOffset>.bin   raw images for lift_ms1_jobmods.py
  jobmod_meta.json                                copy of the committed stable ids
  spu_0002_policy_10135e00.elf                    wrapped PM (id 3); also copied to
                                                  --images when given
  jobbootstrap.bin                                unregistered, informational
  named_manifest.json                             offsets, sizes, entries, identities

Usage:
  python scripts/spu/extract_ms1_named.py --elf EBOOT.elf --out build/extracted/named
                                          [--images build/extracted/spu]
"""
import argparse
import hashlib
import json
import os
import shutil
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.abspath(os.path.join(HERE, '..', '..', 'vendor', 'ps3recomp', 'tools'))
sys.path.insert(0, TOOLS)
from wrap_spu_elf import wrap  # noqa: E402

META = os.path.join(HERE, 'jobmod_meta.json')
PT_LOAD = 1
SHT_SYMTAB = 2
STT_OBJECT = 1
SHN_ABS = 0xFFF1
EM_PPC64 = 21
JOB_KINDS = ('wwsjob_image',)
PM_STEM, PM_PAD, PM_BASE = 'jobmanager_file', 16, 0xA00
PM_NAME = 'spu_0002_policy_10135e00'
BOOTSTRAP_STEM = 'jobbootstrap_file'


def fnv1a64(data):
    h = 1469598103934665603
    for x in data:
        h = ((h ^ x) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


class Elf:
    """Minimal big-endian ELF64 reader: program headers, sections, .symtab."""

    def __init__(self, data):
        if data[:4] != b'\x7fELF' or data[4] != 2 or data[5] != 2:
            raise SystemExit('Input is not a big-endian ELF64 executable')
        machine = struct.unpack_from('>H', data, 0x12)[0]
        if machine != EM_PPC64:
            raise SystemExit(f'Input e_machine {machine} is not EM_PPC64')
        self.data = data
        phoff, shoff = struct.unpack_from('>QQ', data, 0x20)
        phentsize, phnum, shentsize, shnum = struct.unpack_from('>HHHH', data, 0x36)
        self.loads = []
        for i in range(phnum):
            o = phoff + i * phentsize
            p_type, _flags, p_offset, p_vaddr, _paddr, p_filesz = struct.unpack_from('>IIQQQQ', data, o)
            if p_type == PT_LOAD and p_filesz:
                self.loads.append((p_vaddr, p_offset, p_filesz))
        self.sections = []
        for i in range(shnum):
            o = shoff + i * shentsize
            name, sh_type, _flags, _addr, off, size, link = struct.unpack_from('>IIQQQQI', data, o)
            self.sections.append(dict(name=name, type=sh_type, offset=off, size=size, link=link))
        symtabs = [s for s in self.sections if s['type'] == SHT_SYMTAB]
        if not symtabs:
            raise SystemExit('No .symtab: a stripped/retail EBOOT cannot drive the named extraction')
        symtab = symtabs[0]
        strtab = self.sections[symtab['link']]
        strings = data[strtab['offset']:strtab['offset'] + strtab['size']]
        self.symbols = {}
        for o in range(symtab['offset'], symtab['offset'] + symtab['size'], 24):
            st_name, st_info, _other, st_shndx, st_value, st_size = struct.unpack_from('>IBBHQQ', data, o)
            end = strings.find(b'\0', st_name)
            name = strings[st_name:end].decode('ascii', 'replace')
            if name:
                self.symbols.setdefault(name, dict(value=st_value, size=st_size,
                                                    type=st_info & 0xF, shndx=st_shndx))

    def file_offset(self, ea):
        for vaddr, offset, filesz in self.loads:
            if vaddr <= ea < vaddr + filesz:
                return offset + (ea - vaddr)
        raise SystemExit(f'EA 0x{ea:X} lies outside every file-backed PT_LOAD')

    def binary_blob(self, stem):
        start = self.symbols.get(f'_binary_{stem}_start')
        size = self.symbols.get(f'_binary_{stem}_size')
        if not start or not size:
            raise SystemExit(f'Missing _binary_{stem}_start/_size symbols')
        if size['shndx'] != SHN_ABS:
            raise SystemExit(f'_binary_{stem}_size is not an absolute symbol')
        return start['value'], size['value']


def job_header(raw, label):
    if len(raw) < 0x30 or raw[0x20:0x24] != b'\xC0\xDE\xC0\xDE':
        raise SystemExit(f'{label}: no C0DEC0DE marker at +0x20')
    if raw[0] not in (0x42, 0x43):
        raise SystemExit(f'{label}: leading word is not an ila marker')
    entry_off, image_size, bss = struct.unpack_from('>III', raw, 0x10)
    link = struct.unpack_from('>I', raw, 0x28)[0]
    if not link or link % 0x400:
        raise SystemExit(f'{label}: implausible link base 0x{link:X}')
    if not 0x30 <= entry_off < image_size:
        raise SystemExit(f'{label}: entry offset 0x{entry_off:X} outside image 0x{image_size:X}')
    return entry_off, image_size, bss, link


def job_candidates(elf):
    """(stem, symbol, EA, symtab size) for every named job image."""
    found = []
    for name, sym in elf.symbols.items():
        for kind in JOB_KINDS:
            suffix = f'_{kind}_start'
            if name.startswith('_binary_') and name.endswith(suffix):
                stem = name[len('_binary_'):-len('_start')]
                ea, size = elf.binary_blob(stem)
                found.append((name[len('_binary_'):-len(suffix)], name, ea, size))
        if (sym['type'] == STT_OBJECT and 0x400 <= sym['size'] <= 0x40000
                and sym['value'] % 16 == 0 and sym['value']):
            try:
                off = elf.file_offset(sym['value'])
            except SystemExit:
                continue
            if elf.data[off + 0x20:off + 0x24] == b'\xC0\xDE\xC0\xDE':
                found.append((name, name, sym['value'], sym['size']))
    unique = {}
    for candidate in found:
        unique.setdefault(candidate[2], candidate)
    return sorted(unique.values(), key=lambda c: c[2])


def sweep_unnamed(elf, named_offsets):
    """C0DEC0DE headers in file-backed LOADs that no symbol names."""
    extra = []
    for _vaddr, offset, filesz in elf.loads:
        pos = elf.data.find(b'\xC0\xDE\xC0\xDE', offset, offset + filesz)
        while pos >= 0:
            start = pos - 0x20
            if start >= offset and start % 16 == 0 and start not in named_offsets \
                    and elf.data[start] in (0x42, 0x43):
                extra.append(start)
            pos = elf.data.find(b'\xC0\xDE\xC0\xDE', pos + 4, offset + filesz)
    return extra


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--elf', required=True, help='user-supplied named debug EBOOT (ELF64)')
    ap.add_argument('--out', required=True, help='ignored output directory')
    ap.add_argument('--images', help='also place the wrapped PM ELF beside the class-A SPU ELFs')
    ap.add_argument('--meta', default=META, help='stable ids/names/identities (default: committed copy)')
    args = ap.parse_args()

    with open(args.meta, encoding='utf-8') as f:
        meta = json.load(f)
    expected = meta['identities']
    elf = Elf(open(args.elf, 'rb').read())
    jobs_dir = os.path.join(args.out, 'jobmods')
    if os.path.isdir(jobs_dir) and os.listdir(jobs_dir):
        raise SystemExit(f'{jobs_dir} is not empty; move it aside before regeneration')
    os.makedirs(jobs_dir, exist_ok=True)

    errors, manifest = [], []
    stems = {}
    for stem, symbol, ea, sym_size in job_candidates(elf):
        off = elf.file_offset(ea)
        entry_off, image_size, bss, link = job_header(elf.data[off:off + 0x30], symbol)
        if image_size != sym_size + 0x20:
            errors.append(f'{symbol}: header imageSize 0x{image_size:X} != symtab size 0x{sym_size:X} + 0x20')
            continue
        raw = elf.data[off:off + image_size]
        sha = hashlib.sha256(raw).hexdigest()
        name = f'jobmod_{sha[:12]}_e{entry_off:x}'
        staged = fnv1a64(raw[:image_size - 0x20])
        want = expected.get(name)
        if not want:
            errors.append(f'{symbol}: {name} has no committed identity')
        elif int(want['staged_fnv'], 16) != staged or want['symbol'] != symbol:
            errors.append(f'{name}: expected {want["symbol"]} staged {want["staged_fnv"]}, '
                          f'got {symbol} {staged:016X}')
        stems[off] = name
        with open(os.path.join(jobs_dir, name + '.bin'), 'wb') as f:
            f.write(raw)
        manifest.append(dict(name=name, kind='wwsjob', symbol=symbol, stem=stem,
                             id=meta['ids'].get(name), reg_name=meta['names'].get(name),
                             guest_ea=f'0x{ea:08X}', file_offset=f'0x{off:08X}',
                             image_size=image_size, entry_offset=f'0x{entry_off:X}',
                             entry=f'0x{link + entry_off:X}', link_base=f'0x{link:X}',
                             bss_size=f'0x{bss:X}', staged_fnv=f'{staged:016X}', sha256=sha))
    missing = sorted(set(expected) - set(m['name'] for m in manifest) - {PM_NAME})
    if missing:
        errors.append('Committed identities not found in this EBOOT: ' + ', '.join(missing))
    extra = sweep_unnamed(elf, set(stems))
    if extra:
        print('WARNING: unnamed C0DEC0DE headers at file offsets ' +
              ', '.join(f'0x{o:X}' for o in extra))

    ea, size = elf.binary_blob(PM_STEM)
    off = elf.file_offset(ea)
    payload = elf.data[off:off + size] + b'\0' * PM_PAD
    pm = wrap(payload, base=PM_BASE, entry=PM_BASE)
    want = expected[PM_NAME]
    pm_whole, pm_text = fnv1a64(pm), fnv1a64(payload)
    if (pm_whole, pm_text) != (int(want['whole_fnv'], 16), int(want['text_fnv'], 16)):
        errors.append(f'{PM_NAME}: expected whole {want["whole_fnv"]} text {want["text_fnv"]}, '
                      f'got {pm_whole:016X} {pm_text:016X}')
    manifest.append(dict(name=PM_NAME, kind='policy', symbol=f'_binary_{PM_STEM}_start', id=3,
                         guest_ea=f'0x{ea:08X}', file_offset=f'0x{off:08X}', template_size=size,
                         wrap_base=f'0x{PM_BASE:X}', whole_fnv=f'{pm_whole:016X}',
                         text_fnv=f'{pm_text:016X}', sha256=hashlib.sha256(pm).hexdigest()))

    ea, size = elf.binary_blob(BOOTSTRAP_STEM)
    off = elf.file_offset(ea)
    bootstrap = elf.data[off:off + size]
    manifest.append(dict(name='jobbootstrap', kind='unregistered', symbol=f'_binary_{BOOTSTRAP_STEM}_start',
                         guest_ea=f'0x{ea:08X}', file_offset=f'0x{off:08X}', size=size,
                         whole_fnv=f'{fnv1a64(bootstrap):016X}'))

    if errors:
        shutil.rmtree(jobs_dir)
        raise SystemExit('Named SPU extraction rejected:\n  ' + '\n  '.join(errors))

    with open(os.path.join(args.out, PM_NAME + '.elf'), 'wb') as f:
        f.write(pm)
    if args.images:
        os.makedirs(args.images, exist_ok=True)
        shutil.copyfile(os.path.join(args.out, PM_NAME + '.elf'), os.path.join(args.images, PM_NAME + '.elf'))
    with open(os.path.join(args.out, 'jobbootstrap.bin'), 'wb') as f:
        f.write(bootstrap)
    # lift_ms1_jobmods.py reads <jobmods>/../jobmod_meta.json and may append ids
    # in place; keep that write inside the ignored output tree.
    shutil.copyfile(args.meta, os.path.join(args.out, 'jobmod_meta.json'))
    with open(os.path.join(args.out, 'named_manifest.json'), 'w', encoding='utf-8', newline='\n') as f:
        json.dump(dict(elf_sha256=hashlib.sha256(elf.data).hexdigest(), images=manifest), f, indent=2)
        f.write('\n')
    jobs = [m for m in manifest if m['kind'] == 'wwsjob']
    for m in jobs:
        print(f"  id {m['id']} {m['reg_name']:18} {m['name']:30} staged {m['staged_fnv']}")
    print(f'PASS: {len(jobs)} job images, policy module and bootstrap match committed identities; '
          f'output {args.out}')


if __name__ == '__main__':
    main()
