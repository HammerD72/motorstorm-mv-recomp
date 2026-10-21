# Independent native world-transform properties

[Issue 186](https://github.com/robbe1912/motorstorm-mv-recomp/issues/186) adds a
standalone public correctness test around the typed
[world-transform API](../src/motorstorm/world_transforms.h). It compiles the
existing kernel as read-only input, with new authored fixtures. No ELF, game
assets, generated code, private captures, GPU or native game session is needed.

## Run on Windows or Linux

Use Python 3.10+, a C++17 compiler, and an **x86-64 AVX2/FMA CPU with OS support
for XMM/YMM state**. No Python packages are required. From an x64 Visual Studio
developer shell on Windows, with LLVM installed:

```powershell
$env:MV_CLANG = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\Llvm\x64\bin\clang-cl.exe'
python scripts/check_native_world_properties.py
python scripts/check_native_world_transforms.py
```

Select the compiler path actually installed on your host. On Linux:

```sh
CXX=c++ python3 scripts/check_native_world_properties.py
CXX=c++ python3 scripts/check_native_world_transforms.py
```

The new runner's `--compiler-style gnu` also supports Windows MinGW clang++.
`MV_CLANG` takes precedence over `CXX`; each variable names one compiler
executable, without embedded shell flags. The runner retains the existing gate's
strict settings: `/O2 /arch:AVX2 /fp:strict` on Windows, or
`-O2 -mavx2 -mfma -ffp-contract=off -frounding-math` with GNU-style compilers
(plus `-mfxsr` for the FXSAVE caller-state probe).
Assertions remain enabled. Fast math is disabled; warnings are errors.

A separately compiled **baseline** capability probe checks CPUID and guarded
XGETBV before entering any AVX-compiled executable. Unsupported architecture,
OS, CPU or OS vector state is reported as `UNSUPPORTED`, with exit code **77**,
never as a pass. Compile failures, unexpected crashes, timeouts, failed properties
or surviving mutants exit nonzero. Each compiler invocation is bounded to 120
seconds; each property executable to 30 seconds; the probe to 10 seconds.

All run directories, compiler commands, build/run logs, mutant sources,
reproducers and the result manifest remain in ignored
`build/self-tests/native-world-properties/run-*/`. The runner hashes the production
kernel/header and original rounding runner/fixture before and after execution;
any modification fails the check. It changes no build configuration or CI YAML.
The two commands above are the standalone gates for later CI integration.

## Independent exact arithmetic domain

Each affine transform contains a signed permutation of the three axes and an
integer translation in quarter units, each component in `[-32,32]` (world units
`[-8,8]`). The homogeneous last column is `[0,0,0,1]`. All signed axis
permutations, including reflections, are allowed. Each forest has at most 64
nodes. Products preserve signed permutation bases; along a complete path,
translations remain integer quarters with absolute components at most 2048
quarters (512 world units). Oracle basis points add at most four quarters.
All products and intermediate sums in this domain are exactly representable
as ordinary binary32 values, far from overflow, underflow and precision loss.

The oracle sends the origin and three unit basis points along the complete
node-to-root ancestry using **integer coordinate permutations, sign changes and
translations**. Differences of the transformed points yield basis rows; the
transformed origin yields translation. It does not use matrix multiplication,
production SIMD, paired fused products, the production rounding sequence or its
root-relative staging. Root ownership is found independently by walking parents.
There are no tolerances. Zero is compared numerically: signed-zero bit rules,
rounding/fusion and unrestricted IEEE-754 behavior belong to the unchanged
`check_native_world_transforms.py` gate, not to these finite-domain properties.

This is not SPU equivalence, measured performance improvement, runtime integration,
native gameplay acceptance or sustained 30 FPS proof. See
[native game operations](NATIVE_GAME.md) for that separate evidence and scope.

## Forest and state coverage

The default seed is `0x186c0ffee` (6555762670). SplitMix64 with explicit unsigned
arithmetic and modulo selection makes generation reproducible across compilers;
it does not use implementation-dependent standard distributions.

The 512 generated cases repeat eight shape buckets 64 times: empty, singleton,
multi-root, 64-node chain, wide tree, balanced tree, random forest, and all roots.
Every case runs in its original valid order and a randomly chosen topological
order of the same graph, for 1024 evaluated orderings. Chains and singleton/empty
cases naturally have fewer distinct valid orderings. Multiple roots have distinct
authored translations, and each forest has independently generated initial and
updated transforms.

Every ordering runs four phases:

1. Clean no-op with sentinel pending outputs and initially uncomputed roots.
2. Topology dirty with transforms initially clean: compute all roots/matrices
   and clear both flags.
3. Transforms-only update with the computed root ownership retained.
4. Clean no-op after inputs change again: retain the previous completed output.

For each phase, check matrices/roots where applicable, dirty flags, descriptor
identity, unchanged local/parent buffers and output guard elements. Empty cases
use null data pointers and zero count, as permitted by the API.

The test reads the hardware MXCSR mask using FXSAVE and uses the Intel SDM's
`0x0000ffbf` fallback when that mask is zero. It exercises all four rounding modes
and supported combinations of DAZ/FTZ, with masked exceptions and all six sticky
status flags set on entry. Typically this is 16 caller modes. The exact **entire
MXCSR value** must be restored immediately after every normal or exceptional call.
See [Intel's architecture manual](https://cdrdv2-public.intel.com/868137/325462-089-sdm-vol-1-2abcd-3abcd-4.pdf)
for the mask and register contract.

Seven minimal authored invalid cases cover a forward parent, out-of-range parent,
wrong root at a root, wrong root at a child, infinity, finite overflow, and a
topology-dirty failure after a preceding root may have been written. They require
`std::invalid_argument` for parent/root failures and `std::domain_error` for numeric
failures, plus unchanged inputs/guards and exact caller MXCSR restoration.
All buffers are owned and valid; invalid indices are rejected before use.
Pending output contents and flags are deliberately discarded after an exception:
the raw kernel does not promise transactional rollback.

## Seeds, failure reduction and replay

```sh
python scripts/check_native_world_properties.py --seed 0
python scripts/check_native_world_properties.py --seed 0xffffffffffffffff
python scripts/check_native_world_properties.py --seed 0x186c0ffee --case-index 2
python scripts/check_native_world_properties.py --replay /path/to/reproducer.txt
```

Seeds are unsigned 64-bit integers, decimal or `0x` hexadecimal. Failure output
contains seed, case, ordering, phase, caller MXCSR and the first differing lane
with expected/actual bit patterns. A greedy reducer removes subtrees and
reindexes the remaining nodes until **no single subtree removal preserves the
same failed property, phase and mode**. This is subtree-minimal, not a claim of
the globally smallest transform values. The complete reduced forest is printed
between `BEGIN_REPRODUCER`/`END_REPRODUCER` and saved to an authored text file;
the executable replay command is printed alongside it.

`WORLD_FOREST_V1` files contain a node count, followed by one line per node:
parent, then initial axis[3]/sign[3]/quarter[3], then updated versions of those
three arrays (19 integers per node). Replay validates count, parent order, axis
permutations, signs and domain bounds before evaluating. `INVALID_CASE_V1` files
contain one of the seven fixed case indices (0..6) and a supported caller MXCSR;
those cases already have only one or two nodes. Replay checks them without
requiring rollback. Selected-case and replay successes explicitly say
`EXPLORATION ONLY`; they do not claim the full forest/mutation gate passed.

Two temporary implementation controls must fail the independent matrix oracle:
using `translations[0]` for every root, and reversing parent/local multiplication.
They compile separately, are stored only under ignored output, and never alter
production source. Both should reduce to two-node cases. Compiler failures or
an unrelated failing property do not count as successful mutation rejection.
