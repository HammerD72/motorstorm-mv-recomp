"""Emit executable STT_FUNC boundaries from a symbol-bearing PPU ELF.

Function descriptors in .opd are data, so are excluded. For zero-sized
symbols, use the next executable symbol or section end as the bound.
"""
import argparse
import json
from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile
from elftools.elf.sections import SymbolTableSection


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        with args.elf.open("rb") as source:
            functions, zero_sized = function_bounds(ELFFile(source))
        args.out.write_text(json.dumps(functions, indent=2), encoding="utf-8")
    except (OSError, ELFError, ValueError) as error:
        parser.error(f"{args.elf}: {error}")
    print(json.dumps({"elf": str(args.elf), "executable_function_starts": len(functions),
                      "zero_sized_bounds": zero_sized, "output": str(args.out)}))


def function_bounds(elf):
    symbols = elf.get_section_by_name(".symtab")
    if symbols is None:
        raise ValueError("ELF has no .symtab")
    if not isinstance(symbols, SymbolTableSection):
        raise ValueError(".symtab is not a symbol table")
    entry_size = elf.structs.Elf_Sym.sizeof()
    if symbols["sh_entsize"] != entry_size:
        raise ValueError(f".symtab entry size must be {entry_size}")
    if not symbols["sh_size"]:
        raise ValueError("empty .symtab: missing reserved null symbol")
    if symbols["sh_size"] % entry_size:
        raise ValueError(f".symtab size is not a multiple of {entry_size}")
    by_section = {}
    for symbol in symbols.iter_symbols():
        index = symbol["st_shndx"]
        if symbol["st_info"]["type"] != "STT_FUNC" or not isinstance(index, int):
            continue
        if not 0 <= index < elf.num_sections():
            raise ValueError(f"invalid section index {index} for function {symbol.name!r}")
        section = elf.get_section(index)
        if not section["sh_flags"] & 4:
            continue
        section_start = section["sh_addr"]
        section_end = section_start + section["sh_size"]
        if not section["sh_size"]:
            raise ValueError(f"empty executable section {section.name!r}")
        if section_end > 1 << elf.elfclass:
            raise ValueError(f"invalid executable section range for {section.name!r}")
        if not section_start <= symbol["st_value"] < section_end:
            raise ValueError(f"function {symbol.name!r} at {symbol['st_value']:#x} outside executable "
                             f"section {section.name!r} [{section_start:#x}, {section_end:#x})")
        by_section.setdefault(index, {}).setdefault(symbol["st_value"], []).append(symbol)
    functions = []
    zero_sized = 0
    for index, starts in by_section.items():
        section = elf.get_section(index)
        section_end = section["sh_addr"] + section["sh_size"]
        ordered = sorted(starts)
        for position, start in enumerate(ordered):
            sizes = [symbol["st_size"] for symbol in starts[start] if symbol["st_size"]]
            following = ordered[position + 1] if position + 1 < len(ordered) else section_end
            if sizes:
                end = start + max(sizes)
                if end > section_end:
                    raise ValueError(f"function at {start:#x} ends at {end:#x}, past section end "
                                     f"{section_end:#x} in {section.name!r}")
                if end > following:
                    raise ValueError(f"function at {start:#x} ends at {end:#x}, past next executable "
                                     f"function at {following:#x} in {section.name!r}")
            else:
                end = following
                zero_sized += 1
            if end <= start:
                raise ValueError(f"empty function at {start:#x}")
            functions.append({"start": hex(start), "end": hex(end)})
    functions.sort(key=lambda item: int(item["start"], 16))
    return functions, zero_sized


if __name__ == "__main__":
    main()
