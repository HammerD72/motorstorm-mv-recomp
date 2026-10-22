// Independently authored baseline probe. Compile WITHOUT AVX/FMA flags.
#include <cstdint>
#include <cstdio>
#if defined(_MSC_VER)
#include <intrin.h>
#elif defined(__x86_64__)
#include <cpuid.h>
#endif

#if defined(_MSC_VER) && defined(__clang__)
__attribute__((target("xsave")))
#endif
static std::uint64_t vector_state() {
#if defined(_MSC_VER)
    return _xgetbv(0);
#elif defined(__x86_64__)
    unsigned lo, hi;
    __asm__ volatile("xgetbv" : "=a"(lo), "=d"(hi) : "c"(0));
    return (std::uint64_t(hi) << 32) | lo;
#else
    return 0;
#endif
}

int main() {
    unsigned ecx = 0, ebx = 0;
#if defined(_MSC_VER) && defined(_M_X64)
    int r[4];
    __cpuidex(r, 0, 0);
    if (r[0] < 7) return 77;
    __cpuidex(r, 1, 0); ecx = static_cast<unsigned>(r[2]);
    __cpuidex(r, 7, 0); ebx = static_cast<unsigned>(r[1]);
#elif defined(__x86_64__)
    if (__get_cpuid_max(0, nullptr) < 7) return 77;
    unsigned a, b, c, d;
    __cpuid_count(1, 0, a, b, c, d); ecx = c;
    __cpuid_count(7, 0, a, b, c, d); ebx = b;
#else
    std::puts("UNSUPPORTED: requires x86-64");
    return 77;
#endif
    // AVX, FMA, XSAVE and OSXSAVE must precede the guarded XGETBV.
    constexpr unsigned required = (1u << 28) | (1u << 12) | (1u << 26) | (1u << 27);
    if ((ecx & required) != required || !(ebx & (1u << 5))) {
        std::puts("UNSUPPORTED: requires AVX2/FMA and OSXSAVE");
        return 77;
    }
    if ((vector_state() & 6) != 6) {
        std::puts("UNSUPPORTED: OS must enable XMM and YMM state");
        return 77;
    }
    std::puts("SUPPORTED: x86-64 AVX2/FMA; OS XMM/YMM state enabled");
    return 0;
}
