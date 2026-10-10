"""Emit executable STT_FUNC boundaries from a symbol-bearing PPU ELF.

Function descriptors in .opd are data, so are excluded. For zero-sized
symbols, use the next executable symbol or section end as the bound.
"""
import argparse
import json
from pathlib import Path

from elftools.elf.elffile import ELFFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with args.elf.open("rb") as source:
        elf = ELFFile(source)
        symbols = elf.get_section_by_name(".symtab")
        if symbols is None:
            parser.error("ELF has no .symtab")
        by_section = {}
        for symbol in symbols.iter_symbols():
            index = symbol["st_shndx"]
            if symbol["st_info"]["type"] != "STT_FUNC" or not isinstance(index, int):
                continue
            section = elf.get_section(index)
            if not section["sh_flags"] & 4:
                continue
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
                    if end > following or end > section_end:
                        raise ValueError(f"overlapping function at {start:#x}")
                else:
                    end = following
                    zero_sized += 1
                if end <= start:
                    raise ValueError(f"empty function at {start:#x}")
                functions.append({"start": hex(start), "end": hex(end)})
    functions.sort(key=lambda item: int(item["start"], 16))
    args.out.write_text(json.dumps(functions, indent=2), encoding="utf-8")
    print(json.dumps({"elf": str(args.elf), "executable_function_starts": len(functions),
                      "zero_sized_bounds": zero_sized, "output": str(args.out)}))


if __name__ == "__main__":
    main()
