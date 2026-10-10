# Repository boundary and audit

Fresh main repository has no old game-bearing history. Allowed content: handwritten src/scripts, root build/config example, docs and curated vendor runtime/lifter source. build/, temp/, game/ and config/local.json are private. Original migration archives/history and external F: originals remain local.

The audit checks the **actual Git index**, including force-added files. It rejects unapproved paths, binaries/media, private folders/config, generated guest names/headers and known bundled key signatures. Local pre-commit hook and GitHub source-audit workflow run the same check. Documentation links/source ownership are checked separately.

```powershell
python scripts/check_repository.py --self-test
python scripts/check_repository.py
python scripts/check_docs.py
```

The root allowlist reduces accidental publication; the index check catches force-add mistakes. Historical original audio/hidden ELF/lifted C++ fixtures exercise rejection. Review arbitrary textual arrays/fixtures by provenance: format checks alone cannot certify them. Private key audit signatures contain digests, not key bytes.

Initial audit scope also checks staged blobs against original ELF/disc/firmware/key input patterns, reviews large source arrays as algorithmic or excludes uncertain fixtures, confirms initial commit parent count0 and examines the final commit tree. The old history is never pushed. Audit artifacts/byte manifests are private temp/migration records.

All 64 original notes were moved with hashes preserved, then consolidated by topic with a coverage table. Public findings omit original instruction/asset excerpts; unchanged originals remain private evidence. Compiler/run/generated outputs never enter Git.

Local migration validation: the final curated index has 397 source files. Known ELF/disc/firmware/key/fixture inputs are compared by size/hash, in addition to binary/path/key-signature checks. A separate ignored index/object store proves rejection of real ELF/audio bytes, generated guest C++, disguised key headers, a force-added private path and old vendor diagnostic output. No original test blob enters the repository's object store. Native build completed with EXE SHA256 `9ac375764870ccffdee3af8a1f34add8b3cfac5ad3f8c780feaa3f9003a2f531`; this proves compilation only. Missing key headers/tests-on errors, private-key loading, setup, Python syntax and eight CLI help checks passed. Fifteen documentation pages link correctly; all 64 preserved note hashes match. Reports remain in temp/migration/.

These checks establish what the commit contains under their stated scope. They do not provide legal clearance or guarantee that another party will never assert a claim. Users must supply game inputs they are legally entitled to use; the source license does not license those inputs. Do not upload game binaries, generated guest code, keys, memory dumps or captures in pull requests, issues, release assets or CI artifacts. Review diagnostic logs before sharing them. Retain the component notices described in [vendor provenance](VENDOR.md).

The publication approach was checked against these projects on October 10, 2026:

| Reference | Relevant practice |
| --- | --- |
| [ps3recomp Legal](https://github.com/sp00nznet/ps3recomp#legal) | Describes clean-room system interfaces and requires user-supplied legally obtained game files; excludes proprietary Sony code and encryption keys. |
| [UnleashedRecomp](https://github.com/hedge-dev/UnleashedRecomp#readme) | States that game assets are not included and requires a legally acquired copy for installation or building. |
| [XenonRecomp usage](https://github.com/hedge-dev/XenonRecomp#usage) | Accepts a local executable as input to its translation tools. Its README does not establish a legal-immunity policy. |

These are examples of distribution practices, not rulings on this project's legality. This repository additionally excludes generated guest translations from its public source tree and enforces the reviewed boundary locally and in CI.
