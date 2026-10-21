"""Public subprocess regression for the actual debug ELF boundary CLI.

Run: python scripts/check_debug_elf_functions.py
Dependency: pyelftools>=0.31 (already listed in requirements.txt).
All binary inputs are constructed locally from authored metadata and cleaned up.
"""
from dataclasses import replace
import io
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

try:
    from elftools.elf.elffile import ELFFile
except ImportError:
    raise SystemExit('Install the test dependency: python -m pip install "pyelftools>=0.31"')

from fixtures.debug_elf_fixture import EXPECTED, SECTIONS, SYMBOLS, Section, Symbol, build_elf

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/analysis/debug_elf_functions.py'


class DebugElfChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = ROOT / 'temp/self-tests/debug-elf-functions'
        base.mkdir(parents=True, exist_ok=True)
        cls.folder = Path(tempfile.mkdtemp(prefix='authored-', dir=base))
        cls.base = base.resolve()
        cls.owned = set()
        cls.sequence = itertools.count()
        cls.addClassCleanup(cls.cleanup_owned)

    @classmethod
    def cleanup_owned(cls):
        # Flat, explicitly tracked files only: never recursively traverse a
        # worktree, caller-provided folder, symlink or directory junction.
        if cls.folder.is_symlink() or getattr(cls.folder, 'is_junction', lambda: False)():
            raise RuntimeError('test output directory became a link; refusing cleanup')
        if cls.folder.resolve().parent != cls.base or not cls.folder.name.startswith('authored-'):
            raise RuntimeError('test output escaped its owned parent; refusing cleanup')
        for path in sorted(cls.owned):
            if path.parent != cls.folder or path.resolve().parent != cls.folder.resolve():
                raise RuntimeError('test file escaped owned output; refusing cleanup')
            path.unlink(missing_ok=True)
        cls.folder.rmdir()  # Fails safely if an unowned entry appeared.

    def owned_path(self, name):
        path = self.folder / name
        self.owned.add(path)
        return path

    def cli(self, data, *, script=SCRIPT, previous=None):
        index = next(self.sequence)
        source = self.owned_path(f'authored input {index}.elf')
        output = self.owned_path(f'boundaries {index}.json')
        source.write_bytes(data)
        if previous is not None:
            output.write_bytes(previous)
        result = subprocess.run([sys.executable, str(script), str(source), '--out', str(output)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(source.read_bytes(), data, 'CLI modified its ELF input')
        return result, source, output

    def assert_success(self, call, expected, zero_sized):
        result, source, output = call
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, '')
        # Preserve the CLI's existing text-mode newline convention on each OS.
        expected_bytes = json.dumps(expected, indent=2).replace('\n', os.linesep).encode('utf-8')
        self.assertEqual(output.read_bytes(), expected_bytes)
        self.assertEqual(json.loads(result.stdout), dict(elf=str(source), executable_function_starts=len(expected),
                                                       zero_sized_bounds=zero_sized, output=str(output)))

    def assert_invalid(self, data, diagnostic, *, previous=None):
        result, source, output = self.cli(data, previous=previous)
        self.assertNotEqual(result.returncode, 0, 'Malformed ELF was accepted')
        self.assertEqual(result.stdout, '', 'Malformed input emitted a success summary')
        self.assertIn(str(source), result.stderr)
        self.assertIn(diagnostic, result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        if previous is None:
            self.assertFalse(output.exists(), 'Malformed input produced a partial output')
        else:
            self.assertEqual(output.read_bytes(), previous, 'Failure overwrote the previous output')

    def test_fixture_is_actual_elf64_big_endian_ppc64(self):
        image = build_elf(SECTIONS, SYMBOLS)
        elf = ELFFile(io.BytesIO(image.data))
        self.assertEqual(elf.elfclass, 64)
        self.assertFalse(elf.little_endian)
        self.assertEqual(elf.header['e_machine'], 'EM_PPC64')
        self.assertEqual(elf.get_section_by_name('.text').data(), bytes(0x30))
        self.assertEqual(elf.get_section_by_name('.symtab').num_symbols(), len(SYMBOLS) + 1)
        self.assertLess(len(image.data), 4096)

    def test_exact_ordered_boundaries_and_summary(self):
        image = build_elf(SECTIONS, SYMBOLS)
        self.assert_success(self.cli(image.data), EXPECTED, 4)
        self.assert_success(self.cli(image.data), EXPECTED, 4)

    def test_section_symbol_and_alias_order_do_not_change_json(self):
        for sections, symbols in [(SECTIONS[::-1], SYMBOLS[::-1]),
                                   (SECTIONS[1:] + SECTIONS[:1], SYMBOLS[5:] + SYMBOLS[:5])]:
            with self.subTest(order=[s.name for s in sections]):
                self.assert_success(self.cli(build_elf(sections, symbols).data), EXPECTED, 4)

    def test_alias_max_size_and_zero_group_section_end(self):
        image = build_elf([Section('.init', 0x80, 0x20)],
                          [Symbol('short', '.init', 0x84, 4), Symbol('long', '.init', 0x84, 8),
                           Symbol('zero_alias', '.init', 0x84, 0), Symbol('tail', '.init', 0x90, 0),
                           Symbol('tail_alias', '.init', 0x90, 0)])
        self.assert_success(self.cli(image.data), [{'start': '0x84', 'end': '0x8c'},
                                                  {'start': '0x90', 'end': '0xa0'}], 1)

    def test_only_non_executable_or_non_function_symbols_is_empty(self):
        image = build_elf(SECTIONS, [s for s in SYMBOLS if s.section in ('.opd', '.data') or s.kind != 2])
        self.assert_success(self.cli(image.data), [], 0)
        self.assert_success(self.cli(build_elf(SECTIONS).data), [], 0)

    def test_missing_symtab(self):
        self.assert_invalid(build_elf(SECTIONS, symtab=False).data, 'ELF has no .symtab')

    def test_sized_extent_crosses_next_function_or_section_end(self):
        for name, size, diagnostic in [('hot_main', 12, 'next executable function'),
                                        ('hot_tail', 0x24, 'section end')]:
            with self.subTest(name=name):
                symbols = [replace(s, size=size) if s.name == name else s for s in SYMBOLS]
                self.assert_invalid(build_elf(SECTIONS, symbols).data, diagnostic)

    def test_starts_outside_claimed_executable_section(self):
        for start, size in [(0xffc, 4), (0xff8, 0), (0x1010, 0), (0x1014, 4)]:
            with self.subTest(start=hex(start), size=size):
                image = build_elf([Section('.text', 0x1000, 0x10)], [Symbol('outside', '.text', start, size)])
                self.assert_invalid(image.data, 'outside executable section')

    def test_empty_or_wrapped_section_extent(self):
        for section, diagnostic in [(Section('.text', 0x1000, 0), 'empty executable section'),
                                     (Section('.text', (1 << 64)-8, 16), 'invalid executable section range')]:
            with self.subTest(section=section):
                image = build_elf([section], [Symbol('bad_extent', '.text', section.address, 0)])
                self.assert_invalid(image.data, diagnostic)

    def test_invalid_symbol_section_index(self):
        image = build_elf(SECTIONS, [Symbol('bad_index', 42, 0x1000, 4)])
        self.assert_invalid(image.data, 'invalid section index 42')

    def test_malformed_symbol_table_headers(self):
        image = build_elf(SECTIONS, SYMBOLS)
        for values, diagnostic in [({'kind': 1}, '.symtab is not a symbol table'),
                                    ({'size': 0}, 'empty .symtab'),
                                    ({'size': 25}, 'multiple'),
                                    ({'entry_size': 0}, 'error:'),
                                    ({'offset': len(image.data) + 64}, 'error:')]:
            with self.subTest(values=values):
                self.assert_invalid(image.changed_header('.symtab', **values), diagnostic)

    def test_malformed_or_truncated_elf_and_existing_output(self):
        image = build_elf(SECTIONS, SYMBOLS)
        for data in (b'', b'authored non-ELF input', image.data[:32], image.data[:-20]):
            with self.subTest(size=len(data)):
                self.assert_invalid(data, 'error:')
        symbols = [replace(s, size=12) if s.name == 'hot_main' else s for s in SYMBOLS]
        previous = json.dumps(EXPECTED, indent=2).encode('utf-8')
        self.assert_invalid(build_elf(SECTIONS, symbols).data, 'next executable function', previous=previous)

    def test_non_executable_admission_mutant_is_rejected(self):
        text = SCRIPT.read_text(encoding='utf-8')
        anchor = 'if not section["sh_flags"] & 4:'
        self.assertEqual(text.count(anchor), 1, 'non-executable admission control anchor changed')
        mutant = self.owned_path('non executable admission mutant.py')
        mutant.write_text(text.replace(anchor, 'if False:  # deliberate admission defect', 1), encoding='utf-8')
        call = self.cli(build_elf(SECTIONS, SYMBOLS).data, script=mutant)
        result, _, output = call
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        actual = json.loads(output.read_text(encoding='utf-8'))
        self.assertTrue(any(int(row['start'], 16) in (0x5000, 0x6000) for row in actual))
        # Use the same exact-JSON/summary oracle as the positive subprocesses.
        # Compilation/parser errors cannot stand in for mutation detection.
        with self.assertRaises(AssertionError):
            self.assert_success(call, EXPECTED, 4)


if __name__ == '__main__':
    unittest.main(verbosity=2)
