// (longshot) 8- and 16-bit stores through the real shader-info pass and EmitSPIRV, no GPU or game files:
// tests/test_narrow_global_store.py builds this with the back end's sources and runs it with a folder for the modules.
// Each case is a small compute program; its module is written as <case>.spv and checked here.
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <utility>
#include <vector>

#include <spirv/unified1/spirv.hpp11>   // (Sirit's: scoped enumerants; spirv.hpp shares its guard and would win if first)
#include "shader_recompiler/backend/spirv/emit_spirv.h"
#include "shader_recompiler/environment.h"
#include "shader_recompiler/frontend/ir/ir_emitter.h"
#include "shader_recompiler/ir_opt/passes.h"
#include "shader_recompiler/object_pool.h"

using namespace Shader;
using Shader::Backend::SPIRV::EmitSPIRV;

namespace {

std::string out_dir;

struct TestEnvironment final : Environment {
    TestEnvironment() { stage = Stage::Compute; }
    u64 ReadInstruction(u32) override { return 0; }
    u32 ReadCbufValue(u32, u32) override { return 0; }
    TextureType ReadTextureType(u32) override { return TextureType::Color2D; }
    TexturePixelFormat ReadTexturePixelFormat(u32) override { return TexturePixelFormat::R8_UNORM; }
    bool IsTexturePixelFormatInteger(u32) override { return false; }
    u32 ReadViewportTransformState() override { return 0; }
    u32 TextureBoundBuffer() const override { return 0; }
    u32 LocalMemorySize() const override { return 0; }
    u32 SharedMemorySize() const override { return 0; }
    std::array<u32, 3> WorkgroupSize() const override { return {1, 1, 1}; }
    bool HasHLEMacroState() const override { return false; }
    std::optional<ReplaceConstant> GetReplaceConstBuffer(u32, u32) override {
        return std::nullopt;
    }
    void Dump(u64, u64) override {}
};

[[noreturn]] void fail(const char* what, const char* name) {
    std::fprintf(stderr, "FAIL: %s: %s\n", name, what);
    std::exit(1);
}

void expect(bool ok, const char* what, const char* name) {
    if (!ok) fail(what, name);
}

// (the program's stores)
enum : u32 { GLOBAL_U8 = 1, GLOBAL_U16 = 2, GLOBAL_32 = 4, STORAGE_U8 = 8, STORAGE_U16 = 16 };

struct Inst {
    spv::Op op;
    std::vector<u32> w;
};
struct Function {
    u32 id;
    size_t first, last;   // (its OpFunction and OpFunctionEnd)
    std::vector<u32> params;
    std::vector<u32> param_types;
};
struct Module {
    std::vector<Inst> insts;
    std::vector<Function> functions;
    std::map<u32, std::pair<u32, u32>> ints;       // (id: width, signedness)
    std::map<u32, std::pair<u32, u32>> pointers;   // (id: storage class, pointee)
    std::map<u32, u32> chains;                     // (an access chain's result: its pointer type)
    std::map<u32, u64> constants;
    std::set<u32> capabilities;
    std::set<std::string> extensions;
    u32 entry{};
};

Module parse(const std::vector<u32>& code, const char* name) {
    if (code.size() < 5 || code[0] != spv::MagicNumber) fail("no SPIR-V header", name);
    Module m;
    for (size_t at = 5; at < code.size();) {
        const u32 count{code[at] >> 16};
        if (count == 0 || at + count > code.size()) fail("bad instruction length", name);
        Inst inst{static_cast<spv::Op>(code[at] & 0xffffu), std::vector<u32>(code.begin() + at + 1, code.begin() + at + count)};
        at += count;
        const std::vector<u32>& w{inst.w};
        switch (inst.op) {
        case spv::Op::OpCapability:
            m.capabilities.insert(w.at(0));
            break;
        case spv::Op::OpExtension:   // (a literal string, little-endian, ending in a zero byte)
            m.extensions.insert(std::string(reinterpret_cast<const char*>(w.data())));
            break;
        case spv::Op::OpEntryPoint:
            m.entry = w.at(1);
            break;
        case spv::Op::OpTypeInt:
            m.ints[w.at(0)] = {w.at(1), w.at(2)};
            break;
        case spv::Op::OpTypePointer:
            m.pointers[w.at(0)] = {w.at(1), w.at(2)};
            break;
        case spv::Op::OpConstant:
            m.constants[w.at(1)] = w.at(2) | (w.size() > 3 ? u64{w[3]} << 32 : u64{0});
            break;
        case spv::Op::OpAccessChain:
            m.chains[w.at(1)] = w.at(0);
            break;
        case spv::Op::OpFunction:
            m.functions.push_back({w.at(1), m.insts.size(), 0, {}});
            break;
        case spv::Op::OpFunctionParameter:
            m.functions.back().params.push_back(w.at(1));
            m.functions.back().param_types.push_back(w.at(0));
            break;
        case spv::Op::OpFunctionEnd:
            m.functions.back().last = m.insts.size();
            break;
        default:
            break;
        }
        m.insts.push_back(std::move(inst));
    }
    return m;
}

const Function& function(const Module& m, u32 id, const char* name) {
    for (const Function& f : m.functions) {
        if (f.id == id) return f;
    }
    fail("no such function", name);
}

// (the global compare-exchange loop's helper: (u64 address, value, bit offset, width), with its bit-field insert. Not the
// storage buffers' loop, which takes a pointer: it is made too where the profile has no 8- or 16-bit integers)
const Function& loop_function(const Module& m, const char* name) {
    for (const Function& f : m.functions) {
        if (f.params.size() != 4) continue;
        const auto address{m.ints.find(f.param_types[0])};
        if (address == m.ints.end() || address->second.first != 64) continue;
        for (size_t i = f.first; i <= f.last; ++i) {
            if (m.insts[i].op == spv::Op::OpBitFieldInsert) return f;
        }
    }
    fail("no compare-exchange loop helper", name);
}

std::set<u32> calls(const Module& m, const Function& f) {
    std::set<u32> out;
    for (size_t i = f.first; i <= f.last; ++i) {
        if (m.insts[i].op == spv::Op::OpFunctionCall) out.insert(m.insts[i].w.at(2));
    }
    return out;
}

// (plain stores of a width-bit integer through an access chain into that storage class)
int stores(const Module& m, u32 width, spv::StorageClass storage) {
    int n = 0;
    for (const Inst& inst : m.insts) {
        if (inst.op != spv::Op::OpStore) continue;
        const auto chain{m.chains.find(inst.w.at(0))};
        if (chain == m.chains.end()) continue;
        const auto pointer{m.pointers.find(chain->second)};
        if (pointer == m.pointers.end() || pointer->second.first != static_cast<u32>(storage)) continue;
        const auto value{m.ints.find(pointer->second.second)};
        n += value != m.ints.end() && value->second.first == width;
    }
    return n;
}

// (whether the module declares bits-wide storage: the capability and its extension go together)
bool declared(const Module& m, u32 bits, const char* name) {
    const auto capability{bits == 8 ? spv::Capability::StorageBuffer8BitAccess : spv::Capability::StorageBuffer16BitAccess};
    const bool cap{m.capabilities.count(static_cast<u32>(capability)) != 0};
    const bool ext{m.extensions.count(bits == 8 ? "SPV_KHR_8bit_storage" : "SPV_KHR_16bit_storage") != 0};
    expect(cap == ext, "the 8/16-bit storage capability and its extension are not declared together", name);
    return cap;
}

Module emit(const char* name, u32 what, u64 address, bool ssbo) {
    ObjectPool<IR::Inst> inst_pool;
    IR::Block block{inst_pool};
    IR::Program program;
    program.stage = Stage::Compute;
    program.workgroup_size = {1, 1, 1};
    program.blocks.push_back(&block);
    program.post_order_blocks.push_back(&block);
    IR::AbstractSyntaxNode node{};
    node.type = IR::AbstractSyntaxNode::Type::Block;
    node.data.block = &block;
    program.syntax_list.push_back(node);
    IR::AbstractSyntaxNode ret{};
    ret.type = IR::AbstractSyntaxNode::Type::Return;
    program.syntax_list.push_back(ret);

    IR::IREmitter ir{block};
    if (what & GLOBAL_U8) ir.WriteGlobalU8(ir.Imm64(address), ir.Imm32(0xA5u));
    if (what & GLOBAL_U16) ir.WriteGlobalU16(ir.Imm64(address & ~u64{1}), ir.Imm32(0xA5A5u));
    if (what & GLOBAL_32) ir.WriteGlobal32(ir.Imm64(address & ~u64{3}), ir.Imm32(0xA5A5A5A5u));
    if (what & STORAGE_U8) {   // (a byte stored at offset 0 of storage buffer 0, through its u8 view)
        block.AppendNewInst(IR::Opcode::WriteStorageU8, {IR::Value{0u}, IR::Value{0u}, IR::Value{0xA5u}});
    }
    if (what & STORAGE_U16) {   // (a half at offset 2, through its u16 view)
        block.AppendNewInst(IR::Opcode::WriteStorageU16, {IR::Value{0u}, IR::Value{2u}, IR::Value{0xA5A5u}});
    }
    ir.Epilogue();

    TestEnvironment env;
    Optimization::CollectShaderInfoPass(env, program);
    if (ssbo) {   // (one storage buffer, its address and size in c0)
        program.info.constant_buffer_descriptors.push_back({0, 1});
        program.info.storage_buffers_descriptors.push_back({0, 0, 1, true});
        program.info.used_constant_buffer_types |= IR::Type::U32 | IR::Type::U32x2;
    }

    Profile profile{};
    profile.supported_spirv = 0x00010600;
    profile.support_int64 = true;
    profile.support_int8 = true;
    profile.support_int16 = true;
    profile.support_storage_buffer_8bit = profile.support_storage_buffer_16bit = true;
    profile.support_descriptor_aliasing = true;
    profile.min_ssbo_alignment = 16;
    const std::vector<u32> code{EmitSPIRV(profile, program)};
    std::ofstream file(out_dir + "/" + name + ".spv", std::ios::binary);
    file.write(reinterpret_cast<const char*>(code.data()), static_cast<std::streamsize>(code.size() * sizeof(u32)));
    if (!file) fail("could not write the module", name);
    return parse(code, name);
}

} // Anonymous namespace

int main(int argc, char** argv) {
    if (argc != 2) {
        std::fprintf(stderr, "usage: %s OUTPUT_DIRECTORY\n", argv[0]);
        return 2;
    }
    out_dir = argv[1];
    const auto sb{spv::StorageClass::StorageBuffer};
    {   // (the loop's page-table lookup: the byte's address enters it only as its aligned word, address & ~3)
        const char* name{"loop-page-word"};
        const Module m{emit(name, GLOBAL_U8, 0x0000000208000ffdull, false)};
        const Function& loop{loop_function(m, name)};
        const u32 addr{loop.params.at(0)};
        int uses = 0;
        bool masked = false;
        for (size_t i = loop.first + 1; i <= loop.last; ++i) {
            const Inst& inst{m.insts[i]};
            if (inst.op == spv::Op::OpFunctionParameter || std::find(inst.w.begin(), inst.w.end(), addr) == inst.w.end()) {
                continue;
            }
            ++uses;
            const auto mask{inst.w.size() == 4 ? m.constants.find(inst.w[3]) : m.constants.end()};
            masked = inst.op == spv::Op::OpBitwiseAnd && inst.w[2] == addr && mask != m.constants.end() &&
                     mask->second == ~u64{3};
        }
        expect(uses == 1 && masked, "the page-table lookup takes the byte's own address, not its aligned word", name);
        expect(calls(m, function(m, m.entry, name)).count(loop.id) != 0, "the store does not call the loop", name);
        expect(!declared(m, 8, name) && !declared(m, 16, name), "8/16-bit storage declared with no such view", name);
        std::printf("PASS: %s: the loop looks up address & ~3 alone\n", name);
    }
    {   // (a storage buffer's u8 view: the capability and extension it needs)
        const char* name{"storage-u8-view"};
        const Module m{emit(name, STORAGE_U8, 0, true)};
        expect(stores(m, 8, sb) == 1, "no store through the u8 view", name);
        expect(declared(m, 8, name), "an 8-bit storage view without StorageBuffer8BitAccess and SPV_KHR_8bit_storage", name);
        expect(!declared(m, 16, name), "16-bit storage declared with no 16-bit view", name);
        std::printf("PASS: %s: StorageBuffer8BitAccess and SPV_KHR_8bit_storage declared\n", name);
    }
    {   // (a storage buffer's u16 view)
        const char* name{"storage-u16-view"};
        const Module m{emit(name, STORAGE_U16, 0, true)};
        expect(stores(m, 16, sb) == 1, "no store through the u16 view", name);
        expect(declared(m, 16, name), "a 16-bit storage view without StorageBuffer16BitAccess and SPV_KHR_16bit_storage",
               name);
        expect(!declared(m, 8, name), "8-bit storage declared with no 8-bit view", name);
        std::printf("PASS: %s: StorageBuffer16BitAccess and SPV_KHR_16bit_storage declared\n", name);
    }
    {   // (narrow global stores alone make no narrow view: the loop works on the u32 one, nothing declared)
        const char* name{"loop-global-only"};
        const Module m{emit(name, GLOBAL_U8 | GLOBAL_U16 | GLOBAL_32, 0x1000, true)};
        const Function& loop{loop_function(m, name)};
        expect(calls(m, function(m, m.entry, name)).count(loop.id) != 0, "the narrow stores do not call the loop", name);
        expect(stores(m, 8, sb) + stores(m, 16, sb) == 0, "a narrow store through a narrow view", name);
        expect(!declared(m, 8, name) && !declared(m, 16, name), "8/16-bit storage declared with no such view", name);
        std::printf("PASS: %s: the loop, nothing declared\n", name);
    }
    return 0;
}
