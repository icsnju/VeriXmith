// -*- mode: C++; c-file-style: "cc-mode" -*-
//*************************************************************************
// DESCRIPTION: Verilator: Emit C++ for symbolic execution
//
// Code available from: https://verilator.org
//
//*************************************************************************
//
// Copyright 2003-2022 by Wilson Snyder. This program is free software; you
// can redistribute it and/or modify it under the terms of either the GNU
// Lesser General Public License Version 3 or the Perl Artistic License
// Version 2.0.
// SPDX-License-Identifier: LGPL-3.0-only OR Artistic-2.0
//
//*************************************************************************

#include "config_build.h"
#include "verilatedos.h"

#include "V3EmitCSymExecMain.h"

#include "V3EmitC.h"
#include "V3EmitCBase.h"
#include "V3Global.h"

#include <unordered_set>

VL_DEFINE_DEBUG_FUNCTIONS;

//######################################################################

class EmitCSymExecMain final : EmitCBaseVisitor {
private:
    // MEMBERS
    std::unordered_set<const AstVar*> symbolic_vars, non_symbolic_vars, clocks;

    // VISITORS
    void visit(AstCReset* nodep) override {
        AstVar* const varp = nodep->varrefp()->varp();

        if (varp->isSignal()) {
            if (varp->isUsedClock()) {
                clocks.insert(varp);
            } else if (varp->isPrimaryIO() && !varp->isNonOutput()) {
                // For output ports, only write name + width in comments
                non_symbolic_vars.insert(varp);
            } else if (!varp->isHideLocal() && !varp->isFuncLocal()
                       && (varp->isPrimaryInish() || (!varp->isPrimaryIO() && !varp->isNet()))) {
                // Make input ports & internal registers symbolic
                symbolic_vars.insert(varp);
            }
        }
    }
    //--------------------
    // Default: Just iterate
    void visit(AstNode* nodep) override { iterateChildren(nodep); }  // LCOV_EXCL_LINE

public:
    // CONSTRUCTORS
    EmitCSymExecMain(AstNetlist* nodep) { emit(nodep); }

private:
    string emitVarInfo(const AstVar* varp) {
        // Since array style ports are only supported in SystemVerilog,
        // it will be safe to omit recursive iteration over arrays.
        return "// - \"" + varp->nameProtect() + "\"\n";
    }

    string emitClockSetHigh(const AstVar* varp) {
        return "topp->rootp->" + varp->nameProtect() + " = 1;\n";
    }

    string emitVarMadeSymbolic(const string& dataType, const string& name, const string& suffix,
                               const string& offset, int widthMin) {
        string snippet;

        snippet += "{\n";

        string temp_var_name, unique_name;
        if (suffix.size() == 0) {
            temp_var_name = name;
            unique_name = "\"" + name + "\"";
        } else {
            temp_var_name = "temp";
            unique_name = "name";

            snippet += "char* " + unique_name + " = (char *) alloca(" + cvtToStr(name.size() + 20)
                       + ");\n";  // This length is long enough in most cases
            snippet
                += "sprintf(" + unique_name + ", \"%s_%d\", \"" + name + "\", " + offset + ");\n";
        }

        // Define a temporary variable then make it symbolic
        snippet += dataType + " " + temp_var_name + ";\n";
        snippet += "klee_make_symbolic(&" + temp_var_name + ", sizeof(" + temp_var_name + "), "
                   + unique_name + ");\n";
        // Restrict the variable's width
        // Width in [8, 16, 32, 64] don't need this assumption
        if (widthMin != 8 && widthMin != 16 && widthMin != 32 && widthMin != 64)
            snippet += "klee_assume(" + temp_var_name + " < ((" + dataType + ") 1UL << "
                       + cvtToStr(widthMin) + "));\n";
        // Assign the symbolic variable to corresponding field in the Verilated model
        snippet += "topp->rootp->" + name + suffix + " = " + temp_var_name + ";\n";

        snippet += "\n}\n";

        return snippet;
    }

    string emitVarMadeSymbolicRecurse(const AstVar* varp, AstNodeDType* dtypep, int depth,
                                      const string& suffix, const string& offset) {
        dtypep = dtypep->skipRefp();
        AstBasicDType* const basicp = dtypep->basicp();

        if (AstAssocArrayDType* const adtypep = VN_CAST(dtypep, AssocArrayDType)) {
            // Access std::array as C array
            const string cvtarray = (adtypep->subDTypep()->isWide() ? ".data()" : "");
            return emitVarMadeSymbolicRecurse(varp, adtypep->subDTypep(), depth + 1,
                                              suffix + ".atDefault()" + cvtarray, offset);
        } else if (AstWildcardArrayDType* const adtypep = VN_CAST(dtypep, WildcardArrayDType)) {
            // Access std::array as C array
            const string cvtarray = (adtypep->subDTypep()->isWide() ? ".data()" : "");
            return emitVarMadeSymbolicRecurse(varp, adtypep->subDTypep(), depth + 1,
                                              suffix + ".atDefault()" + cvtarray, offset);
        } else if (VN_IS(dtypep, ClassRefDType)) {
            return "";  // Constructor does it
        } else if (const AstDynArrayDType* const adtypep = VN_CAST(dtypep, DynArrayDType)) {
            // Access std::array as C array
            const string cvtarray = (adtypep->subDTypep()->isWide() ? ".data()" : "");
            return emitVarMadeSymbolicRecurse(varp, adtypep->subDTypep(), depth + 1,
                                              suffix + ".atDefault()" + cvtarray, offset);
        } else if (const AstQueueDType* const adtypep = VN_CAST(dtypep, QueueDType)) {
            // Access std::array as C array
            const string cvtarray = (adtypep->subDTypep()->isWide() ? ".data()" : "");
            return emitVarMadeSymbolicRecurse(varp, adtypep->subDTypep(), depth + 1,
                                              suffix + ".atDefault()" + cvtarray, offset);
        } else if (const AstUnpackArrayDType* const adtypep = VN_CAST(dtypep, UnpackArrayDType)) {
            UASSERT_OBJ(adtypep->hi() >= adtypep->lo(), varp,
                        "Should have swapped msb & lsb earlier.");
            const string ivar = string("__Vi") + cvtToStr(depth);
            const string elements = cvtToStr(adtypep->elementsConst());
            const string pre = ("for (int " + ivar + "=" + cvtToStr(0) + "; " + ivar + "<"
                                + elements + "; ++" + ivar + ") {\n");
            const string below = emitVarMadeSymbolicRecurse(
                varp, adtypep->subDTypep(), depth + 1, suffix + "[" + ivar + "]",
                "(" + offset + ")" + "*" + elements + "+" + ivar);
            const string post = "}\n";
            return below.empty() ? "" : pre + below + post;
        } else if (basicp && basicp->keyword() == VBasicDTypeKwd::STRING) {
            return "";
        } else if (basicp && basicp->isForkSync()) {
            return "";
        } else if (basicp && basicp->isDelayScheduler()) {
            return "";
        } else if (basicp && basicp->isTriggerScheduler()) {
            return "";
        } else if (basicp) {
            if (dtypep->isWide()) {  // Handle unpacked; not basicp->isWide
                string out;
                const int widthWords = varp->widthWords();
                for (int w = 0; w < widthWords; ++w) {
                    int widthMin = (w == widthWords - 1) ? varp->widthMin() % 32 : 32;
                    out += emitVarMadeSymbolic(
                        "EData", varp->nameProtect(), suffix + "[" + cvtToStr(w) + "]",
                        "(" + offset + ")" + "*" + cvtToStr(widthWords) + "+" + cvtToStr(w),
                        widthMin);
                }
                return out;
            } else {
                string data_type = (varp->isQuad())          ? "QData"
                                   : (varp->widthMin() > 16) ? "IData"
                                   : (varp->widthMin() > 8)  ? "SData"
                                                             : "CData";
                return emitVarMadeSymbolic(data_type, varp->nameProtect(), suffix, offset,
                                           varp->widthMin());
            }
        } else {
            v3fatalSrc("Unknown node type in main generator: " << varp->prettyTypeName());
        }
        return "";
    }

    // MAIN METHOD
    void emit(AstNetlist* nodep) {
        const string filename = v3Global.opt.makeDir() + "/" + topClassName() + "__main.cpp";
        newCFile(filename, false /*slow*/, true /*source*/);
        V3OutCFile cf{filename};
        m_ofp = &cf;

        // Not defining main_time/vl_time_stamp, so
        v3Global.opt.addCFlags("-DVL_TIME_CONTEXT");  // On MSVC++ anyways

        ofp()->putsHeader();
        puts("// DESCRIPTION: main() function created with Verilator --sym-exec-main\n");
        puts("\n");

        puts("#include \"verilated.h\"\n");
        puts("#include \"" + topClassName() + ".h\"\n");
        puts("#include \"" + topClassName() + "___024root.h\"\n");
        puts("\n#include <klee/klee.h>\n");

        puts("\n//======================\n\n");

        puts("int main(int argc, char** argv, char**) {\n");
        puts("// Setup context, defaults, and parse command line\n");
        puts("Verilated::debug(0);\n");

        // Create VerilatedContext object
        puts("VerilatedContext* contextp = new VerilatedContext;\n");
        puts("contextp->commandArgs(argc, argv);\n");
        puts("\n");

        puts("// Construct the Verilated model, from Vtop.h generated from Verilating\n");
        puts(topClassName() + "* topp = new " + topClassName() + "(contextp);\n");
        puts("\n");

        puts("// Evaluate initials\n");
        puts("topp->eval();  // Evaluate\n");
        puts("\n");

        // Set symbolic variables

        iterate(nodep);

        puts("// Symbolic variables:\n");
        for (auto var : symbolic_vars) { puts(emitVarInfo(var)); }
        for (auto var : symbolic_vars) {
            puts(emitVarMadeSymbolicRecurse(var, var->dtypep()->skipRefp(), 0, "", "0"));
        }
        puts("\n");

        puts("// Output ports:\n");
        for (auto var : non_symbolic_vars) { puts(emitVarInfo(var)); }
        puts("\n");

        // Save the first snapshot before the positive edge of the clock
        puts("klee_save_snapshot(topp->vlSymsp);\n");
        puts("\n");

        // Set clock value high

        for (auto var : clocks) { puts(emitClockSetHigh(var)); }
        puts("\n");

        puts(/**/ "// Evaluate model\n");
        puts(/**/ "topp->eval();\n");
        puts(/**/ "// Advance time\n");
        if (v3Global.rootp()->delaySchedulerp()) {
            puts("if (!topp->eventsPending()) break;\n");
            puts("contextp->time(topp->nextTimeSlot());\n");
        } else {
            puts("contextp->timeInc(1);\n");
        }

        puts("\n");

        // Save the second snapshot after the positive edge of the clock
        puts("klee_save_snapshot(topp->vlSymsp);\n");
        puts("\n");

        puts("// Final model cleanup\n");
        puts("topp->final();\n");
        puts("return 0;\n");
        puts("}\n");

        m_ofp = nullptr;
    }
};

//######################################################################
// EmitC (symbolic execution) class functions

void V3EmitCSymExecMain::emit(AstNetlist* nodep) {
    UINFO(2, __FUNCTION__ << ": " << endl);
    { EmitCSymExecMain{nodep}; }
}
