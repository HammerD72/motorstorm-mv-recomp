// Authored exact-domain properties; no original instructions or game inputs.
#include "world_transforms.h"
#include <immintrin.h>
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

using namespace motorstorm::game;
using Point = std::array<std::int64_t, 3>;
constexpr unsigned max_nodes = 64;
struct Transform {
    std::array<int, 3> axis{{0, 1, 2}}, sign{{1, 1, 1}}, quarter{{0, 0, 0}};
};
struct Node { unsigned parent; Transform initial, updated; };
using Forest = std::vector<Node>;
struct Random {
    std::uint64_t state;
    std::uint64_t next() {
        // SplitMix64, explicitly unsigned: same sequence on both platforms.
        std::uint64_t z = (state += UINT64_C(0x9e3779b97f4a7c15));
        z = (z ^ (z >> 30)) * UINT64_C(0xbf58476d1ce4e5b9);
        z = (z ^ (z >> 27)) * UINT64_C(0x94d049bb133111eb);
        return z ^ (z >> 31);
    }
    unsigned below(unsigned n) { return static_cast<unsigned>(next() % n); }
};
std::uint32_t bits(float f) { std::uint32_t b; std::memcpy(&b, &f, 4); return b; }
float from_bits(std::uint32_t b) { float f; std::memcpy(&f, &b, 4); return f; }
Matrix4 matrix(const Transform& t) {
    Matrix4 m{};
    for (unsigned i = 0; i < 3; ++i) {
        m.row[i].value[t.axis[i]] = static_cast<float>(t.sign[i]);
        m.row[3].value[i] = static_cast<float>(t.quarter[i]) / 4;
    }
    m.row[3].value[3] = 1;
    return m;
}

// Oracle: move four points along the complete ancestor path using integer
// coordinate permutations/signs/translations. No matrix product, FP sums,
// SIMD intrinsics, paired products or production root-relative staging.
Point point_to_world(const Forest& forest, unsigned node, Point point, bool updated) {
    for (;;) {
        const auto& n = forest[node];
        const auto& t = updated ? n.updated : n.initial;
        Point result{{t.quarter[0], t.quarter[1], t.quarter[2]}};
        for (unsigned axis = 0; axis < 3; ++axis)
            result[t.axis[axis]] += t.sign[axis] * point[axis];
        point = result;
        if (n.parent == node) return point;
        node = n.parent;
    }
}
Matrix4 oracle(const Forest& forest, unsigned node, bool updated) {
    Matrix4 m{};
    const auto origin = point_to_world(forest, node, {{0, 0, 0}}, updated);
    for (unsigned row = 0; row < 3; ++row) {
        Point basis{{0, 0, 0}}; basis[row] = 4;
        const auto point = point_to_world(forest, node, basis, updated);
        for (unsigned col = 0; col < 3; ++col)
            m.row[row].value[col] = static_cast<float>(point[col] - origin[col]) / 4;
    }
    for (unsigned col = 0; col < 3; ++col)
        m.row[3].value[col] = static_cast<float>(origin[col]) / 4;
    m.row[3].value[3] = 1;
    return m;
}
unsigned owner(const Forest& forest, unsigned i) {
    while (forest[i].parent != i) i = forest[i].parent;
    return i;
}
Transform transform(Random& random) {
    Transform t;
    for (unsigned i = 3; i > 1; --i) std::swap(t.axis[i-1], t.axis[random.below(i)]);
    for (unsigned i = 0; i < 3; ++i) {
        t.sign[i] = random.below(2) ? 1 : -1;
        t.quarter[i] = static_cast<int>(random.below(65)) - 32;
    }
    return t;
}
Forest generate(Random& random, unsigned case_index) {
    const auto shape = case_index % 8;
    const auto n = shape == 0 ? 0 : shape == 1 ? 1 : shape == 3 ? max_nodes : 3 + random.below(max_nodes - 2);
    Forest forest;
    for (unsigned i = 0; i < n; ++i) {
        unsigned parent = i;
        if (i && shape != 7) {
            if (shape == 2) parent = i < 3 ? i : random.below(i);
            else if (shape == 3) parent = i-1;
            else if (shape == 4) parent = 0;
            else if (shape == 5) parent = (i-1)/2;
            else parent = random.below(4) ? random.below(i) : i;
        }
        Node node{parent, transform(random), transform(random)};
        if (parent == i) {
            // Different root translations are mandatory, including updates.
            node.initial.quarter[0] = i % 2 ? 28 : -28;
            node.updated.quarter[0] = i % 2 ? -27 : 27;
        }
        forest.push_back(node);
    }
    return forest;
}
Forest reordered(const Forest& input, Random& random) {
    Forest output;
    std::vector<unsigned> mapped(input.size(), static_cast<unsigned>(input.size()));
    while (output.size() < input.size()) {
        std::vector<unsigned> ready;
        for (unsigned i = 0; i < input.size(); ++i)
            if (mapped[i] == input.size() && (input[i].parent == i || mapped[input[i].parent] != input.size())) ready.push_back(i);
        const auto old = ready[random.below(static_cast<unsigned>(ready.size()))];
        mapped[old] = static_cast<unsigned>(output.size());
        Node node = input[old]; node.parent = mapped[node.parent]; output.push_back(node);
    }
    return output;
}

template<class T> struct Buffer {
    std::vector<T> storage;
    explicit Buffer(unsigned n, T fill = {}) : storage(n + 2, fill) {}
    T* data() { return storage.data() + 1; }
    bool same(const std::vector<T>& before) const {
        return storage.size() == before.size() && std::memcmp(storage.data(), before.data(), storage.size()*sizeof(T)) == 0;
    }
    bool guards(const std::vector<T>& before) const {
        return std::memcmp(&storage.front(), &before.front(), sizeof(T)) == 0 &&
               std::memcmp(&storage.back(), &before.back(), sizeof(T)) == 0;
    }
};
enum class Error { none, invalid, domain, unexpected };
struct Call { unsigned mxcsr; Error error; };
std::uint64_t calls = 0, checked_nodes = 0;
Call invoke(WorldTransforms& h, unsigned mode) {
    const auto original = _mm_getcsr();
    _mm_setcsr(mode);
    Call result{mode, Error::none};
    try { evaluate_world_transforms(h); result.mxcsr = _mm_getcsr(); }
    catch (const std::invalid_argument&) { result.mxcsr = _mm_getcsr(); result.error = Error::invalid; }
    catch (const std::domain_error&) { result.mxcsr = _mm_getcsr(); result.error = Error::domain; }
    catch (...) { result.mxcsr = _mm_getcsr(); result.error = Error::unexpected; }
    _mm_setcsr(original);
    ++calls;
    return result;
}
struct Failure {
    std::string property, phase, detail;
    unsigned mode = 0;
    explicit operator bool() const { return !property.empty(); }
};
Failure failure(const char* property, const char* phase, unsigned mode, std::string detail = {}) {
    return {property, phase, std::move(detail), mode};
}
Failure check(const Forest& forest, unsigned mode) {
    const auto n = static_cast<unsigned>(forest.size());
    Matrix4 sentinel{};
    for (auto& row : sentinel.row) for (auto& lane : row.value) lane = 123;
    Buffer<Matrix4> local(n, sentinel), world(n, sentinel);
    Buffer<std::uint32_t> parent(n, 0xa5a5a5a5u), root(n, 0xf00du);
    for (unsigned i = 0; i < n; ++i) { local.data()[i] = matrix(forest[i].initial); parent.data()[i] = forest[i].parent; }
    WorldTransforms h{n, n ? local.data() : nullptr, n ? world.data() : nullptr,
                      n ? parent.data() : nullptr, n ? root.data() : nullptr, false, false};
    // Clean input is deliberately different from pending output, making an
    // accidental evaluation observable even before the first dirty call.
    for (unsigned step = 0; step < 4; ++step) {
        const char* phase = step == 0 ? "clean-initial" : step == 1 ? "topology-dirty" : step == 2 ? "transforms-only" : "clean-updated";
        if (step == 1) h.topology_dirty = true; // transforms_dirty remains false
        if (step == 2) {
            for (unsigned i = 0; i < n; ++i) local.data()[i] = matrix(forest[i].updated);
            h.transforms_dirty = true;
        }
        if (step == 3) for (unsigned i = 0; i < n; ++i) local.data()[i] = matrix(forest[i].initial);
        const auto before_local = local.storage, before_world = world.storage;
        const auto before_parent = parent.storage, before_root = root.storage;
        const auto result = invoke(h, mode);
        if (result.mxcsr != mode) return failure("mxcsr", phase, mode, "expected=" + std::to_string(mode) + " actual=" + std::to_string(result.mxcsr));
        if (result.error != Error::none) return failure("unexpected_exception", phase, mode);
        if (!local.same(before_local) || !parent.same(before_parent)) return failure("immutable_input", phase, mode);
        if (!world.guards(before_world) || !root.guards(before_root)) return failure("output_guard", phase, mode);
        if (h.count != n || h.local != (n ? local.data() : nullptr) || h.world != (n ? world.data() : nullptr) ||
            h.parent != (n ? parent.data() : nullptr) || h.root != (n ? root.data() : nullptr)) return failure("buffer_descriptor", phase, mode);
        if (h.transforms_dirty || h.topology_dirty) return failure("dirty_flags", phase, mode);
        if (step == 0 || step == 3) {
            if (!world.same(before_world) || !root.same(before_root)) return failure("clean_noop", phase, mode);
            continue;
        }
        for (unsigned i = 0; i < n; ++i) {
            if (root.data()[i] != owner(forest, i)) return failure("root_owner", phase, mode, "node=" + std::to_string(i));
            const auto expected = oracle(forest, i, step == 2);
            for (unsigned row = 0; row < 4; ++row) for (unsigned col = 0; col < 4; ++col) {
                const float actual = world.data()[i].row[row].value[col];
                const float want = expected.row[row].value[col];
                // Production preserves root signed zeros and canonicalizes
                // child cancellation zeros. This finite-domain oracle compares
                // numerical zero; the dedicated FP suite owns zero bit rules.
                if (actual != want) return failure("world_matrix", phase, mode,
                    "node=" + std::to_string(i) + " row=" + std::to_string(row) + " col=" + std::to_string(col) +
                    " expected_bits=" + std::to_string(bits(want)) + " actual_bits=" + std::to_string(bits(actual)));
            }
            ++checked_nodes;
        }
    }
    return {};
}

Forest without_subtree(const Forest& input, unsigned removed) {
    Forest result;
    std::vector<unsigned> mapped(input.size(), static_cast<unsigned>(input.size()));
    for (unsigned i = 0; i < input.size(); ++i) {
        if (i == removed || (input[i].parent != i && mapped[input[i].parent] == input.size())) continue;
        mapped[i] = static_cast<unsigned>(result.size());
        Node node = input[i]; node.parent = mapped[node.parent]; result.push_back(node);
    }
    return result;
}
bool same_failure(const Failure& a, const Failure& b) { return b && a.property == b.property && a.phase == b.phase && a.mode == b.mode; }
Forest minimize(Forest forest, const Failure& target) {
    bool reduced = true;
    while (reduced) {
        reduced = false;
        for (unsigned i = 0; i < forest.size(); ++i) {
            auto candidate = without_subtree(forest, i);
            if (same_failure(target, check(candidate, target.mode))) { forest = std::move(candidate); reduced = true; break; }
        }
    }
    return forest;
}
void print_forest(const Forest& forest) {
    std::fprintf(stderr, "BEGIN_REPRODUCER\nWORLD_FOREST_V1\n%u\n", static_cast<unsigned>(forest.size()));
    for (const auto& n : forest) {
        std::fprintf(stderr, "%u", n.parent);
        for (const auto& t : {n.initial, n.updated})
            for (const auto& component : {t.axis, t.sign, t.quarter}) for (int value : component) std::fprintf(stderr, " %d", value);
        std::fprintf(stderr, "\n");
    }
    std::fprintf(stderr, "END_REPRODUCER\n");
}
Forest read_forest(const char* path) {
    std::ifstream stream(path); std::string marker; unsigned n;
    if (!(stream >> marker >> n) || marker != "WORLD_FOREST_V1" || n > max_nodes) throw std::invalid_argument("bad replay header/count");
    Forest forest;
    for (unsigned i = 0; i < n; ++i) {
        Node node{};
        if (!(stream >> node.parent) || node.parent > i) throw std::invalid_argument("bad replay parent");
        for (auto* t : {&node.initial, &node.updated}) {
            for (auto* component : {&t->axis, &t->sign, &t->quarter}) for (int& value : *component)
                if (!(stream >> value)) throw std::invalid_argument("truncated replay transform");
            auto axes = t->axis; std::sort(axes.begin(), axes.end());
            if (axes != std::array<int,3>{{0,1,2}}) throw std::invalid_argument("bad replay axes");
            for (unsigned j = 0; j < 3; ++j)
                if ((t->sign[j] != 1 && t->sign[j] != -1) || t->quarter[j] < -32 || t->quarter[j] > 32) throw std::invalid_argument("replay outside exact domain");
        }
        forest.push_back(node);
    }
    if (stream >> marker) throw std::invalid_argument("trailing replay data");
    return forest;
}

std::vector<unsigned> caller_modes() {
    alignas(16) unsigned char state[512]{};
    _fxsave(state);
    unsigned mask; std::memcpy(&mask, state + 28, 4);
    if (!mask) mask = 0xffbfu; // Intel SDM's legacy MXCSR_MASK fallback.
    std::vector<unsigned> modes;
    for (unsigned round : {0u, 0x2000u, 0x4000u, 0x6000u})
        for (unsigned flush : {0u, 0x40u, 0x8000u, 0x8040u}) {
            const unsigned mode = 0x1f80u | round | flush | 0x3fu;
            if (!(mode & ~mask)) modes.push_back(mode);
        }
    return modes;
}

Failure invalid_case(unsigned variant, unsigned mode) {
    const unsigned n = variant == 3 || variant == 5 || variant == 6 ? 2 : 1;
    Buffer<Matrix4> local(n), world(n);
    Buffer<std::uint32_t> parent(n), root(n);
    for (unsigned i = 0; i < n; ++i) local.data()[i] = matrix(Transform{});
    if (variant == 0) parent.data()[0] = 1;
    if (variant == 1) parent.data()[0] = std::numeric_limits<std::uint32_t>::max();
    if (variant == 2) root.data()[0] = 1;
    if (variant == 3) root.data()[1] = 1;
    if (variant == 4) local.data()[0].row[0].value[0] = from_bits(0x7f800000u);
    if (variant == 5) { local.data()[0].row[0].value[0] = from_bits(0x7f7fffffu); local.data()[1].row[0].value[0] = 2; }
    if (variant == 6) { parent.data()[1] = 2; root.data()[0] = root.data()[1] = 99; }
    const auto before_local = local.storage, before_world = world.storage;
    const auto before_parent = parent.storage, before_root = root.storage;
    WorldTransforms h{n, local.data(), world.data(), parent.data(), root.data(), variant != 6, variant == 6};
    const auto result = invoke(h, mode);
    const auto expected = variant < 4 || variant == 6 ? Error::invalid : Error::domain;
    if (result.mxcsr != mode) return failure("mxcsr", "invalid", mode);
    if (result.error != expected) return failure("expected_exception", "invalid", mode);
    if (!local.same(before_local) || !parent.same(before_parent)) return failure("immutable_input", "invalid", mode);
    if (!world.guards(before_world) || !root.guards(before_root)) return failure("output_guard", "invalid", mode);
    // No assertions about pending output contents or flags after an exception:
    // the header explicitly requires the caller to discard them.
    return {};
}

void invalid_failure(const Failure& error, std::uint64_t seed, unsigned variant) {
    std::fprintf(stderr, "FAIL property=%s phase=invalid seed=%llu invalid_case=%u mxcsr=%u; minimal authored case has %u node(s).\n",
        error.property.c_str(), static_cast<unsigned long long>(seed), variant, error.mode,
        variant == 3 || variant == 5 || variant == 6 ? 2u : 1u);
    std::fprintf(stderr, "BEGIN_REPRODUCER\nINVALID_CASE_V1\n%u %u\nEND_REPRODUCER\n", variant, error.mode);
}

int main(int argc, char** argv) {
    const auto original = _mm_getcsr();
    try {
        if (argc != 4) throw std::invalid_argument("usage: fixture SEED CASE_INDEX REPLAY_OR_DASH");
        const auto seed = std::stoull(argv[1], nullptr, 0);
        const int selected = std::stoi(argv[2]);
        if (selected < -1 || selected >= 512) throw std::invalid_argument("case index must be -1 or 0..511");
        const auto modes = caller_modes();
        if (modes.empty()) throw std::runtime_error("no supported caller MXCSR modes");
        Random random{seed};
        const bool replay = std::string(argv[3]) != "-";
        if (replay) {
            std::ifstream stream(argv[3]); std::string marker;
            stream >> marker;
            if (marker == "INVALID_CASE_V1") {
                unsigned variant, mode;
                if (!(stream >> variant >> mode) || variant > 6 || std::find(modes.begin(), modes.end(), mode) == modes.end() || stream >> marker)
                    throw std::invalid_argument("bad invalid-case replay");
                if (const auto error = invalid_case(variant, mode)) {
                    invalid_failure(error, seed, variant); _mm_setcsr(original); return 1;
                }
                _mm_setcsr(original); std::printf("PASS invalid-case replay seed=%llu invalid_case=%u mxcsr=%u\n", static_cast<unsigned long long>(seed), variant, mode);
                return 0;
            }
        }
        unsigned forests = 0;
        for (unsigned index = 0; index < (replay ? 1u : 512u); ++index) {
            const auto initial = replay ? read_forest(argv[3]) : generate(random, index);
            const auto shuffled = reordered(initial, random);
            if (!replay && selected >= 0 && index != static_cast<unsigned>(selected)) continue;
            for (unsigned order = 0; order < 2; ++order) {
                const auto& forest = order ? shuffled : initial;
                for (unsigned mode : modes) if (const auto error = check(forest, mode)) {
                    const auto small = minimize(forest, error);
                    const auto reduced = check(small, mode);
                    std::fprintf(stderr, "FAIL property=%s phase=%s seed=%llu case=%u order=%u mxcsr=%u %s\n",
                        error.property.c_str(), error.phase.c_str(), static_cast<unsigned long long>(seed), index, order, mode, reduced.detail.c_str());
                    std::fprintf(stderr, "Subtree-minimal reproducer: %u -> %u nodes (same property, phase and mode).\n",
                        static_cast<unsigned>(forest.size()), static_cast<unsigned>(small.size()));
                    print_forest(small); _mm_setcsr(original); return 1;
                }
                ++forests;
            }
        }
        for (unsigned variant = 0; variant < 7; ++variant) for (unsigned mode : modes)
            if (const auto error = invalid_case(variant, mode)) {
                invalid_failure(error, seed, variant);
                _mm_setcsr(original); return 1;
            }
        _mm_setcsr(original);
        std::printf("PASS seed=%llu generated_forests=%u evaluated_orderings=%u caller_modes=%u calls=%llu checked_nodes=%llu invalid_cases=7\n",
            static_cast<unsigned long long>(seed), replay ? 0u : selected >= 0 ? 1u : 512u, forests,
            static_cast<unsigned>(modes.size()), static_cast<unsigned long long>(calls), static_cast<unsigned long long>(checked_nodes));
        return 0;
    } catch (const std::exception& e) {
        _mm_setcsr(original); std::fprintf(stderr, "FAIL harness: %s\n", e.what()); return 2;
    }
}
