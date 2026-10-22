"""Handwritten ELF64/MSB/EM_PPC64 metadata fixtures, constructed with struct.

No assembler, linker, pyelftools writer, original instructions or game data.
Section payloads are zero-filled and are never executed or translated.
"""
from dataclasses import dataclass
import struct


@dataclass(frozen=True)
class Section:
    name: str
    address: int
    size: int
    flags: int = 6  # SHF_ALLOC | SHF_EXECINSTR


@dataclass(frozen=True)
class Symbol:
    name: str
    section: str | int
    start: int
    size: int
    kind: int = 2  # STT_FUNC
    binding: int = 1  # STB_GLOBAL (only the initial null entry is local)


@dataclass(frozen=True)
class Image:
    data: bytes
    headers: dict[str, int]

    def changed_header(self, name, **values):
        """Deliberately damage an ELF64 section header for negative tests."""
        fields = {'kind': (4, '>I'), 'flags': (8, '>Q'), 'address': (16, '>Q'),
                  'offset': (24, '>Q'), 'size': (32, '>Q'), 'entry_size': (56, '>Q')}
        result = bytearray(self.data)
        for field, value in values.items():
            offset, fmt = fields[field]
            struct.pack_into(fmt, result, self.headers[name] + offset, value)
        return bytes(result)


def build_elf(sections, symbols=(), *, symtab=True):
    """Create a tiny section-bearing ELF; numeric section references may be bad."""
    if len(sections) > 16 or len(symbols) > 64 or any(not 0 <= s.size <= 256 for s in sections):
        raise ValueError('fixture construction must remain bounded')
    indices = {s.name: i for i, s in enumerate(sections, 1)}
    if len(indices) != len(sections) or set(indices) & {'.strtab', '.symtab', '.shstrtab'}:
        raise ValueError('fixture section names must be unique and unreserved')
    records = [dict(name=s.name, kind=1, flags=s.flags, address=s.address, data=bytes(s.size),
                    link=0, info=0, alignment=4, entry_size=0) for s in sections]
    if symtab:
        strings = bytearray(b'\0')
        entries = bytearray(24)  # Reserved, completely zero symbol entry.
        for s in symbols:
            name = len(strings)
            strings.extend(s.name.encode('utf-8') + b'\0')
            index = indices[s.section] if isinstance(s.section, str) else s.section
            entries.extend(struct.pack('>IBBHQQ', name, (s.binding << 4) | s.kind, 0, index, s.start, s.size))
        records.append(dict(name='.strtab', kind=3, flags=0, address=0, data=bytes(strings),
                            link=0, info=0, alignment=1, entry_size=0))
        records.append(dict(name='.symtab', kind=2, flags=0, address=0, data=bytes(entries),
                            link=len(records), info=1, alignment=8, entry_size=24))
    names = bytearray(b'\0')
    name_offsets = {}
    for name in [r['name'] for r in records] + ['.shstrtab']:
        name_offsets[name] = len(names)
        names.extend(name.encode('ascii') + b'\0')
    records.append(dict(name='.shstrtab', kind=3, flags=0, address=0, data=bytes(names),
                        link=0, info=0, alignment=1, entry_size=0))
    data = bytearray(64)
    for r in records:
        data.extend(bytes((-len(data)) % r['alignment']))
        r['offset'] = len(data)
        data.extend(r['data'])
    data.extend(bytes((-len(data)) % 8))
    section_table = len(data)
    data.extend(bytes(64))  # Reserved null section header.
    headers = {}
    for r in records:
        headers[r['name']] = len(data)
        data.extend(struct.pack('>IIQQQQIIQQ', name_offsets[r['name']], r['kind'], r['flags'],
                                r['address'], r['offset'], len(r['data']), r['link'], r['info'],
                                r['alignment'], r['entry_size']))
    ident = b'\x7fELF\x02\x02\x01' + bytes(9)
    # ELF64 big endian, ET_EXEC, EM_PPC64=21; metadata only, no program headers.
    data[:64] = struct.pack('>16sHHIQQQIHHHHHH', ident, 2, 21, 1, 0, 0, section_table,
                            1, 64, 0, 0, 64, len(records) + 1, len(records))
    return Image(bytes(data), headers)


SECTIONS = (Section('.text', 0x3000, 0x30), Section('.cold', 0x1000, 0x20),
            Section('.opd', 0x5000, 0x18, 3), Section('.data', 0x6000, 0x10, 3))
# Deliberately unsorted symbol addresses and section encounter order.
SYMBOLS = (Symbol('cold_tail', '.cold', 0x1018, 0),
           Symbol('descriptor', '.opd', 0x5000, 8),
           Symbol('hot_tail', '.text', 0x3010, 0),
           Symbol('hot_short_alias', '.text', 0x3000, 4, binding=2),
           Symbol('data_function_label', '.data', 0x6000, 0),
           Symbol('cold_entry', '.cold', 0x1000, 0),
           Symbol('hot_main', '.text', 0x3000, 8),
           Symbol('cold_sized', '.cold', 0x100c, 4),
           Symbol('hot_middle', '.text', 0x3008, 0),
           Symbol('hot_zero_alias', '.text', 0x3000, 0),
           Symbol('cold_zero_alias', '.cold', 0x1000, 0),
           Symbol('object_inside_code', '.text', 0x300c, 4, kind=1),
           Symbol('undefined_function', 0, 0x3004, 0),
           Symbol('absolute_function', 0xfff1, 0x1004, 0))

# Authored expected answers; not derived with the analyzer or ELF parser.
EXPECTED = [{'start': '0x1000', 'end': '0x100c'},
            {'start': '0x100c', 'end': '0x1010'},
            {'start': '0x1018', 'end': '0x1020'},
            {'start': '0x3000', 'end': '0x3008'},
            {'start': '0x3008', 'end': '0x3010'},
            {'start': '0x3010', 'end': '0x3030'}]

# Symbol encounter order inserts .text into the analyzer's per-section mapping
# before .cold; output must still list the .cold bound first by address.
TEXT_FIRST_SYMBOLS = (Symbol('hot_main', '.text', 0x3000, 8),
                      Symbol('cold_entry', '.cold', 0x1000, 0))
TEXT_FIRST_EXPECTED = [{'start': '0x1000', 'end': '0x1020'},
                       {'start': '0x3000', 'end': '0x3008'}]

# Starts on both sides of 0x10000: lexicographic and numeric order differ.
WIDE_SECTIONS = (Section('.wide', 0x10000, 0x10), Section('.cold', 0x9000, 0x20))
WIDE_SYMBOLS = (Symbol('wide_main', '.wide', 0x10000, 0x10),
                Symbol('cold_entry', '.cold', 0x9000, 0))
WIDE_EXPECTED = [{'start': '0x9000', 'end': '0x9020'},
                 {'start': '0x10000', 'end': '0x10010'}]
