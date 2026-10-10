"""Resolve ELF path strings against an MS1 archive using the upstream RA tool.

Read-only audit; hash matches are candidates, not proof of build compatibility.
"""
import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ra-tool", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--elf", type=Path, action="append", required=True)
    parser.add_argument("--dat", type=Path)
    parser.add_argument("--cnk", type=Path)
    parser.add_argument("--extract", action="append", default=[])
    parser.add_argument("--out-dir", type=Path)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("ra_lzh", args.ra_tool)
    ra = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ra)
    header, entries, _ = ra.load_bin_index(args.index)
    a0, b = ra.hash_params(header)
    by_hash = {}
    for offset, size, hashed in entries:
        by_hash.setdefault(hashed, []).append({"offset": offset, "size": size})
    print(json.dumps({"index": str(args.index), "entries": len(entries),
                      "unique_hashes": len(by_hash), "a0": a0, "b": b}))
    if args.extract:
        if not (args.dat and args.cnk and args.out_dir):
            parser.error("--extract requires --dat, --cnk and --out-dir")
        ra.guard_output(str(args.out_dir))
        sizes, offsets = ra.load_cnk(args.cnk)
        root = args.out_dir.resolve()
        with args.dat.open("rb") as archive:
            for name in args.extract:
                target = (root / name).resolve()
                if not target.is_relative_to(root):
                    parser.error("extraction path escapes output directory")
                copies = by_hash.get(ra.name_hash(name, a0, b))
                if not copies:
                    parser.error(f"archive has no candidate for {name}")
                entry = copies[0]
                start, length = entry["offset"], entry["size"]
                payload = bytearray()
                for index in range(start // 0x8000, (start + length + 0x7fff) // 0x8000):
                    archive.seek(offsets[index])
                    chunk = archive.read(sizes[index])
                    decoded, info = ra.unpack_chunk(chunk)
                    if len(decoded) != info["unpacked_size"]:
                        raise ValueError(f"chunk {index} decode length mismatch")
                    payload.extend(decoded)
                payload = payload[start % 0x8000:start % 0x8000 + length]
                if len(payload) != length:
                    raise ValueError(f"{name}: incomplete extraction")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("xb") as output:
                    output.write(payload)
                print(json.dumps({"extracted": str(target), "bytes": length,
                                  "sha256": hashlib.sha256(payload).hexdigest()}))
    for elf in args.elf:
        candidates = set()
        for match in re.finditer(rb"[\x20-\x7e]{5,}\x00", elf.read_bytes()):
            value = match.group()[:-1].decode("ascii").replace("\\", "/")
            if re.fullmatch(r"[A-Za-z0-9_./ -]+\.[A-Za-z0-9]{2,8}", value):
                candidates.add(value)
        found = []
        for value in sorted(candidates):
            hashed = ra.name_hash(value, a0, b)
            if hashed in by_hash:
                found.append({"path": value, "hash": f"{hashed:08x}",
                              "copies": by_hash[hashed]})
        print(json.dumps({"elf": str(elf), "path_candidates": len(candidates),
                          "matched_candidates": len(found), "matches": found}))


if __name__ == "__main__":
    main()
