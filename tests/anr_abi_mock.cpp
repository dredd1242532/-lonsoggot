// Authored fixture: interface declarations are facts from NVIDIA SDK v310.9.16.
// No SDK implementation, ANR implementation, standard library or GPU work.
extern "C" { int _fltused = 0; } // MSVC's marker for floating-point code; this mock needs no CRT startup.
struct ID3D11Resource;
struct ID3D12Resource;
struct Params {
    virtual void Set(const char*, unsigned long long) = 0;
    virtual void Set(const char*, float) = 0;
    virtual void Set(const char*, double) = 0;
    virtual void Set(const char*, unsigned) = 0;
    virtual void Set(const char*, int) = 0;
    virtual void Set(const char*, ID3D11Resource*) = 0;
    virtual void Set(const char*, ID3D12Resource*) = 0;
    virtual void Set(const char*, void*) = 0;
    virtual unsigned Get(const char*, unsigned long long*) const = 0;
    virtual unsigned Get(const char*, float*) const = 0;
    virtual unsigned Get(const char*, double*) const = 0;
    virtual unsigned Get(const char*, unsigned*) const = 0;
    virtual unsigned Get(const char*, int*) const = 0;
    virtual unsigned Get(const char*, ID3D11Resource**) const = 0;
    virtual unsigned Get(const char*, ID3D12Resource**) const = 0;
    virtual unsigned Get(const char*, void**) const = 0;
    virtual void Reset() = 0;
};

extern "C" __declspec(dllexport) unsigned anr_abi_probe(Params* p) {
    if (!p) return 1;
    int marker11 = 1, marker12 = 2, marker = 3;
    auto* ptr11 = reinterpret_cast<ID3D11Resource*>(&marker11);
    auto* ptr12 = reinterpret_cast<ID3D12Resource*>(&marker12);
    p->Set("u64", 0xfedcba9876543210ULL);
    p->Set("float", 1.25f);
    p->Set("double", 6.125);
    p->Set("unsigned", 4000000001u);
    p->Set("int", -1701);
    p->Set("d3d11", ptr11);
    p->Set("d3d12", ptr12);
    p->Set("pointer", static_cast<void*>(&marker));
    unsigned long long ull = 0;
    float f = 0;
    double d = 0;
    unsigned u = 0;
    int i = 0;
    ID3D11Resource* a = nullptr;
    ID3D12Resource* b = nullptr;
    void* c = nullptr;
    if (p->Get("u64", &ull) != 1 || ull != 0xfedcba9876543210ULL) return 2;
    if (p->Get("float", &f) != 1 || f != 1.25f) return 3;
    if (p->Get("double", &d) != 1 || d != 6.125) return 4;
    if (p->Get("unsigned", &u) != 1 || u != 4000000001u) return 5;
    if (p->Get("int", &i) != 1 || i != -1701) return 6;
    if (p->Get("d3d11", &a) != 1 || a != ptr11) return 7;
    if (p->Get("d3d12", &b) != 1 || b != ptr12) return 8;
    if (p->Get("pointer", &c) != 1 || c != &marker) return 9;
    if (p->Get("d3d11", &c) != 1 || c != ptr11) return 10;
    if (p->Get("d3d12", &a) != 0xbad00000) return 11;
    p->Set("convert", 19u);
    if (p->Get("convert", &d) != 1 || d != 19) return 12;
    p->Set("outside_integer_range", 1e300);
    if (p->Get("outside_integer_range", &u) != 0xbad00000) return 18;
    p->Reset();
    if (p->Get("u64", &ull) != 0xbad00000) return 13;
    if (p->Get("pointer", &c) != 0xbad00000) return 14;
    if (p->Get(nullptr, &c) != 0xbad00005) return 15;
    if (p->Get("missing", static_cast<unsigned*>(nullptr)) != 0xbad00005) return 16;
    p->Set("after_reset", -3);
    return p->Get("after_reset", &i) == 1 && i == -3 ? 0 : 17;
}
