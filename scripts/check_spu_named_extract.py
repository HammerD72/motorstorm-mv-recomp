"""Check the named SPU extraction stage with an authored ELF64, no game bytes.

Builds a synthetic big-endian PPC64 executable whose .symtab names two C0DEC0DE
job images (one _binary_*_wwsjob_image pair, one STT_OBJECT JobCode blob), a
policy-module template and a bootstrap blob, then runs
scripts/spu/extract_ms1_named.py against identities computed for it. The
committed scripts/spu/jobmod_meta.json is checked for stable, unique ids.
Rejections cover a wrong staged identity, a header/symtab size disagreement,
a stripped EBOOT, a committed identity absent from the input, a policy wrap
mismatch and a non-empty output tree.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT / 'scripts/spu/extract_ms1_named.py'
META = ROOT / 'scripts/spu/jobmod_meta.json'
sys.path.insert(0, str(ROOT / 'vendor/ps3recomp/tools'))
from wrap_spu_elf import wrap  # noqa: E402

VADDR, FILE_BASE = 0x10060000, 0x1000
PM_NAME = 'spu_0002_policy_10135e00'


def fnv1a64(data):
    h = 1469598103934665603
    for x in data:
        h = ((h ^ x) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


def job(seed, entry_off, body_words, size_skew=0):
    """Authored C0DEC0DE image: ila markers, header, body, 0x20-byte trailer."""
    body = b''.join(struct.pack('>I', (seed * 0x9E3779B1 + i * 0x01000193) & 0xFFFFFFFF)
                    for i in range(body_words))
    size = 0x30 + len(body) + 0x20
    head = struct.pack('>IIII', 0x42000000 | seed, 0x42000001, 0x42000002, 0x42000003)
    head += struct.pack('>IIII', entry_off, size + size_skew, 0x40, 0)
    head += struct.pack('>IIII', 0xC0DEC0DE, 0, 0x4000, 0)
    return head + body + bytes(range(0x20))


def build_elf(path, blobs, symbols, strip=False):
    """ELF64 BE EM_PPC64 with one PT_LOAD holding every blob (16-aligned)."""
    data, placed = bytearray(), {}
    for name, blob in blobs:
        data += b'\0' * (-len(data) % 16)
        placed[name] = len(data)
        data += blob
    strtab, syms = b'\0', [b'\0' * 24]
    for name, kind, ref, size in symbols:
        value = VADDR + placed[ref] if ref else size
        info, shndx = {'notype': (0x10, 1), 'object': (0x11, 1), 'abs': (0x10, 0xFFF1)}[kind]
        syms.append(struct.pack('>IBBHQQ', len(strtab), info, 0, shndx, value,
                                size if kind == 'object' else 0))
        strtab += name.encode() + b'\0'
    shstr = b'\0.data\0.symtab\0.strtab\0.shstrtab\0'
    symtab = b''.join(syms)
    out = bytearray(FILE_BASE) + data
    sym_off = len(out); out += symtab
    str_off = len(out); out += strtab
    shs_off = len(out); out += shstr
    out += b'\0' * (-len(out) % 8)
    sh_off = len(out)
    sections = [(0, 0, 0, 0, 0, 0, 0, 0),
                (1, 1, VADDR, FILE_BASE, len(data), 0, 0, 0),
                (7, 2, 0, sym_off, len(symtab), 3, 1, 24),
                (15, 3, 0, str_off, len(strtab), 0, 0, 0),
                (23, 3, 0, shs_off, len(shstr), 0, 0, 0)]
    if strip:
        sections = sections[:2] + [(23, 3, 0, shs_off, len(shstr), 0, 0, 0)]
    for name, typ, addr, off, size, link, info, ent in sections:
        out += struct.pack('>IIQQQQIIQQ', name, typ, 0, addr, off, size, link, info, 8, ent)
    ph_off = len(out)
    out += struct.pack('>IIQQQQQQ', 1, 6, FILE_BASE, VADDR, VADDR, len(data), len(data), 0x10000)
    ident = b'\x7fELF\x02\x02\x01' + b'\0' * 9
    out[:64] = ident + struct.pack('>HHIQQQIHHHHHH', 2, 21, 1, VADDR, ph_off, sh_off, 0,
                                    64, 56, 1, 64, len(sections), len(sections) - 1)
    path.write_bytes(bytes(out))
    return placed


def scenario(work, name, *, skew=0, strip=False, mutate=None):
    jobs = {'fakejob': job(1, 0x40, 64, skew), 'fakeJobCode': job(2, 0x80, 300)}
    template = bytes((i * 7) & 0xFF for i in range(0x2B70))
    bootstrap = bytes((i * 3) & 0xFF for i in range(0x2A0))
    blobs = [('fakejob', jobs['fakejob']), ('fakeJobCode', jobs['fakeJobCode']),
             ('pm', template), ('boot', bootstrap)]
    symbols = [('_binary_fakejob_wwsjob_image_start', 'notype', 'fakejob', 0),
               ('_binary_fakejob_wwsjob_image_size', 'abs', None, len(jobs['fakejob']) - 0x20),
               ('fakeJobCode', 'object', 'fakeJobCode', len(jobs['fakeJobCode']) - 0x20),
               ('_binary_jobmanager_file_start', 'notype', 'pm', 0),
               ('_binary_jobmanager_file_size', 'abs', None, len(template)),
               ('_binary_jobbootstrap_file_start', 'notype', 'boot', 0),
               ('_binary_jobbootstrap_file_size', 'abs', None, len(bootstrap))]
    case = work / name
    case.mkdir()
    build_elf(case / 'EBOOT.elf', blobs, symbols, strip)
    meta = {'ids': {}, 'names': {}, 'identities': {}}
    for i, (stem, symbol) in enumerate((('fakejob', '_binary_fakejob_wwsjob_image_start'),
                                        ('fakeJobCode', 'fakeJobCode'))):
        raw = jobs[stem]
        entry = struct.unpack_from('>I', raw, 0x10)[0]
        key = f'jobmod_{hashlib.sha256(raw).hexdigest()[:12]}_e{entry:x}'
        meta['ids'][key], meta['names'][key] = 210 + i, stem
        meta['identities'][key] = {'symbol': symbol, 'staged_fnv': f'{fnv1a64(raw[:-0x20]):016X}'}
    payload = template + b'\0' * 16
    meta['identities'][PM_NAME] = {'symbol': '_binary_jobmanager_file_start', 'id': 3,
                                   'whole_fnv': f'{fnv1a64(wrap(payload, base=0xA00, entry=0xA00)):016X}',
                                   'text_fnv': f'{fnv1a64(payload):016X}'}
    if mutate:
        mutate(meta)
    (case / 'meta.json').write_text(json.dumps(meta, indent=2), encoding='utf-8')
    ran = subprocess.run([sys.executable, '-I', str(EXTRACT), '--elf', str(case / 'EBOOT.elf'),
                          '--out', str(case / 'out'), '--images', str(case / 'images'),
                          '--meta', str(case / 'meta.json')], capture_output=True, text=True, timeout=60)
    return case, meta, ran


def committed_meta_contract():
    meta = json.loads(META.read_text(encoding='utf-8'))
    ids = meta['ids']
    assert len(set(ids.values())) == len(ids), 'duplicate stable ids'
    assert sorted(ids.values()) == list(range(210, 222)), 'ids 210-221 must stay assigned'
    jobs = {k: v for k, v in meta['identities'].items() if k.startswith('jobmod_')}
    assert len(jobs) == 10 and all(k in ids and k in meta['names'] for k in jobs)
    assert all(len(v['staged_fnv']) == 16 for v in jobs.values())
    assert meta['identities'][PM_NAME]['id'] == 3
    return len(jobs)


def main():
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    work = ROOT / 'temp/checks' / ('spu-named-' + stamp)
    work.mkdir(parents=True)
    results = []

    def expect(name, ran, ok, text):
        output = ran.stdout + ran.stderr
        passed = (ran.returncode == 0) == ok and text in output
        results.append(dict(name=name, returncode=ran.returncode, passed=passed))
        (work / (name + '.log')).write_text(output, encoding='utf-8')
        if not passed:
            raise SystemExit(f'FAIL {name}: rc={ran.returncode}\n{output}')
        print(f'{name}: PASS' + ('' if ok else ' (expected rejection)'))

    jobs = committed_meta_contract()
    print(f'committed-meta: PASS ({jobs} job identities, ids 210-221 unique, policy id 3)')

    case, meta, ran = scenario(work, 'authored')
    expect('authored', ran, True, 'PASS: 2 job images')
    names = sorted(p.stem for p in (case / 'out/jobmods').glob('jobmod_*.bin'))
    assert names == sorted(k for k in meta['identities'] if k.startswith('jobmod_')), names
    manifest = json.loads((case / 'out/named_manifest.json').read_text())
    for image in manifest['images']:
        if image['kind'] == 'wwsjob':
            raw = (case / 'out/jobmods' / (image['name'] + '.bin')).read_bytes()
            assert image['staged_fnv'] == meta['identities'][image['name']]['staged_fnv']
            assert image['image_size'] == len(raw) and image['link_base'] == '0x4000'
    assert (case / 'images' / (PM_NAME + '.elf')).read_bytes() == (case / 'out' / (PM_NAME + '.elf')).read_bytes()
    assert json.loads((case / 'out/jobmod_meta.json').read_text()) == meta

    first = next(k for k in meta['identities'] if k.startswith('jobmod_'))
    _, _, ran = scenario(work, 'wrong-identity',
                         mutate=lambda m: m['identities'][first].update(staged_fnv='0' * 16))
    expect('wrong-identity', ran, False, 'expected _binary_fakejob_wwsjob_image_start')
    _, _, ran = scenario(work, 'size-disagreement', skew=4)
    expect('size-disagreement', ran, False, '!= symtab size')
    _, _, ran = scenario(work, 'stripped', strip=True)
    expect('stripped', ran, False, 'No .symtab')
    _, _, ran = scenario(work, 'absent-identity',
                         mutate=lambda m: m['identities'].update(jobmod_000000000000_e40={'symbol': 'x', 'staged_fnv': '0' * 16}))
    expect('absent-identity', ran, False, 'not found in this EBOOT: jobmod_000000000000_e40')
    _, _, ran = scenario(work, 'policy-wrap',
                         mutate=lambda m: m['identities'][PM_NAME].update(whole_fnv='0' * 16))
    expect('policy-wrap', ran, False, f'{PM_NAME}: expected whole')
    ran = subprocess.run([sys.executable, '-I', str(EXTRACT), '--elf', str(case / 'EBOOT.elf'),
                          '--out', str(case / 'out'), '--meta', str(case / 'meta.json')],
                         capture_output=True, text=True, timeout=60)
    expect('non-empty-output', ran, False, 'is not empty')
    for rejected in ('wrong-identity', 'size-disagreement', 'absent-identity', 'policy-wrap'):
        assert not (work / rejected / 'out/jobmods').exists(), rejected + ' left partial output'

    (work / 'result.json').write_text(json.dumps(dict(game_inputs=False, checks=results), indent=2))
    shutil.rmtree(work / 'authored' / 'out' / 'jobmods')
    print('PASS: named SPU extraction identities, manifest and rejections; evidence', work)


if __name__ == '__main__':
    main()
