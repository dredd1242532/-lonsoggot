// Original storage for NVIDIA's published parameter ABI (DLSS SDK 9981c635, nvsdk_ngx_params.h).
// Windows x64 MSVC vtable order is checked against an original MSVC-ABI mock provider.
#pragma once
#include <cmath>
#include <cstddef>
#include <limits>
#include <map>
#include <string>
#include <type_traits>
#include <variant>

struct ID3D11Resource;
struct ID3D12Resource;

namespace anr_abi {
using Result = unsigned;
inline constexpr Result success = 1, fail = 0xbad00000, invalid_parameter = 0xbad00005;

class Parameters {
    enum class PointerKind { d3d11, d3d12, any };
    struct Pointer { void* value; PointerKind kind; };
    using Value = std::variant<unsigned long long, float, double, unsigned, int, Pointer>;
    using Slot = void (*)();
    struct Raw { const Slot* vtable; Parameters* owner; };
    std::map<std::string, Value, std::less<>> values_;
    bool failed_ = false;
    Raw raw_{table(), this};

    void put(const char* name, Value value) noexcept {
        if (!name) { failed_ = true; return; }
        try { values_.insert_or_assign(name, value); }
        catch (...) { failed_ = true; } // Never unwind through the foreign provider.
    }
    template<class T> static void set_number(Raw* p, const char* n, T v) noexcept { p->owner->put(n, v); }
    template<PointerKind K, class T> static void set_pointer(Raw* p, const char* n, T* v) noexcept {
        p->owner->put(n, Pointer{v, K});
    }
    template<class T> Result number(const char* name, T* out) const noexcept {
        if (!name || !out) return invalid_parameter;
        const auto found = values_.find(name);
        if (found == values_.end()) return fail;
        return std::visit([out](const auto& v) -> Result {
            if constexpr (std::is_arithmetic_v<std::decay_t<decltype(v)>>) {
                if constexpr (std::is_integral_v<T>) {
                    const long double number = static_cast<long double>(v);
                    if (!std::isfinite(number) || number < std::numeric_limits<T>::lowest()
                        || number > std::numeric_limits<T>::max()) return fail;
                }
                *out = static_cast<T>(v);
                return success;
            }
            else return fail;
        }, found->second);
    }
    template<class T> static Result get_number(const Raw* p, const char* n, T* v) noexcept {
        return p->owner->number(n, v);
    }
    template<PointerKind K, class T> Result pointer(const char* name, T** out) const noexcept {
        if (!name || !out) return invalid_parameter;
        const auto found = values_.find(name);
        if (found == values_.end()) return fail;
        const auto* value = std::get_if<Pointer>(&found->second);
        if (!value || (K != PointerKind::any && value->kind != PointerKind::any && K != value->kind)) return fail;
        *out = static_cast<T*>(value->value);
        return success;
    }
    template<PointerKind K, class T> static Result get_pointer(const Raw* p, const char* n, T** v) noexcept {
        return p->owner->pointer<K>(n, v);
    }
    static void reset(Raw* p) noexcept { p->owner->clear(); }
    static const Slot* table() noexcept {
        // MSVC reverses each overload group; GNU's class vtable must not cross this boundary.
        static const Slot slots[] = {
            reinterpret_cast<Slot>(&set_pointer<PointerKind::any, void>),
            reinterpret_cast<Slot>(&set_pointer<PointerKind::d3d12, ID3D12Resource>),
            reinterpret_cast<Slot>(&set_pointer<PointerKind::d3d11, ID3D11Resource>),
            reinterpret_cast<Slot>(&set_number<int>), reinterpret_cast<Slot>(&set_number<unsigned>),
            reinterpret_cast<Slot>(&set_number<double>), reinterpret_cast<Slot>(&set_number<float>),
            reinterpret_cast<Slot>(&set_number<unsigned long long>),
            reinterpret_cast<Slot>(&get_pointer<PointerKind::any, void>),
            reinterpret_cast<Slot>(&get_pointer<PointerKind::d3d12, ID3D12Resource>),
            reinterpret_cast<Slot>(&get_pointer<PointerKind::d3d11, ID3D11Resource>),
            reinterpret_cast<Slot>(&get_number<int>), reinterpret_cast<Slot>(&get_number<unsigned>),
            reinterpret_cast<Slot>(&get_number<double>), reinterpret_cast<Slot>(&get_number<float>),
            reinterpret_cast<Slot>(&get_number<unsigned long long>), reinterpret_cast<Slot>(&reset)
        };
        static_assert(sizeof slots / sizeof *slots == 17);
        return slots;
    }
public:
    Parameters() = default;
    Parameters(const Parameters&) = delete;
    Parameters& operator=(const Parameters&) = delete;
    void* raw() noexcept { static_assert(sizeof(void*) == 8); return &raw_; }
    bool healthy() const noexcept { return !failed_; }
    void clear() noexcept { values_.clear(); failed_ = false; }
    template<class T> void set(const char* name, T value) noexcept {
        if constexpr (std::is_same_v<T, std::nullptr_t>) set_pointer<PointerKind::any, void>(&raw_, name, nullptr);
        else if constexpr (std::is_pointer_v<T>) {
            constexpr auto kind = std::is_same_v<T, ID3D11Resource*> ? PointerKind::d3d11
                                : std::is_same_v<T, ID3D12Resource*> ? PointerKind::d3d12 : PointerKind::any;
            set_pointer<kind>(&raw_, name, value);
        } else set_number(&raw_, name, value);
    }
    template<class T> Result get(const char* name, T* out) const noexcept {
        if constexpr (std::is_pointer_v<T>) {
            constexpr auto kind = std::is_same_v<T, ID3D11Resource*> ? PointerKind::d3d11
                                : std::is_same_v<T, ID3D12Resource*> ? PointerKind::d3d12 : PointerKind::any;
            return pointer<kind>(name, out);
        } else return number(name, out);
    }
};
} // namespace anr_abi
