# Public debug ELF boundary regression

From the repository root on Windows or Linux:

```sh
python -m pip install "pyelftools>=0.31"
python scripts/check_debug_elf_functions.py
```

Use Python 3.10 or newer, as required by the fixture's type annotations. No game
files, assembler, compiler, native build, Ghidra or GPU are needed. The dependency
is already included in the root `requirements.txt`.

The runner invokes the actual `scripts/analysis/debug_elf_functions.py` CLI in
fresh Python subprocesses. `debug_elf_fixture.py` independently constructs small
ELF64, big-endian, EM_PPC64 files using `struct`; the expected boundaries are
handwritten. The [ELF symbol table specification](https://gabi.xinuos.com/elf/05-symtab.html)
describes the metadata layout. All section payloads are authored zero bytes;
there are no copied game instructions, symbols or generated translations.

The checks cover:

- Executable `.text`, `.cold`, `.init` and `.wide` sections, including section
  and symbol encounter order that differs from ascending virtual addresses.
- Sized functions, aliases with different sizes, mixed zero/sized aliases,
  zero-sized groups bounded by the next function or section end, and gaps.
- Non-executable `.opd` and data symbols, non-function symbols, undefined and
  absolute symbols, and valid empty results.
- Missing/malformed symbol tables, truncated ELF metadata, invalid section
  indices, starts outside their claimed executable section, empty/wrapped
  section ranges, and sized extents crossing another function or the section
  end, including one-byte overhangs.
- The exact boundary-array shape, ascending numeric order across different hex
  widths, unchanged summary
  fields/counts, repeatability and reordered inputs. JSON text retains the CLI's
  existing platform newline convention.
- Nonzero failure with a diagnostic identifying the input file, no success
  summary or partial output, preservation of a previous output, and unchanged
  ELF input bytes.

A deliberate temporary mutation disables the executable-section filter. The
mutated CLI must successfully emit the `.opd`/data entries, and the same success
oracle must reject that output. A syntax error or crashed mutant cannot count
as detection.

Fixtures, output JSON and the temporary mutant are created in a unique
`temp/self-tests/debug-elf-functions/authored-*` directory. Cleanup removes only
the explicitly tracked files and that owned directory, without recursive
deletion or following links. No ELF binary is checked in.

This covers the analyzer's metadata boundary contract for issue #187. It does
not establish lifter executable-section admission (#161), overlapping-tail
translation (#162), PPU/SPU translation completeness, native gameplay or FPS.
