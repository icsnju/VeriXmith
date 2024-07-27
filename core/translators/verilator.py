from collections import namedtuple
from io import TextIOWrapper

from core.circuits.cpp import VerilatorCppCircuit
from core.circuits.verilog import VerilogCircuit
from core.consts import LL_DEBUG_INFO, LL_FILENAME, VERILATOR_VAR_DEF
from core.ir.crossbar import VerilatorCppCrossbar
from core.ir.view import ModelTreeView
from core.thirdparty import verilator_compile, verilator_elaborate
from core.translators.translator import CmdlineOption, MetaTranslator
from core.workspace import get_workspace


class VerilatorTransformer(MetaTranslator):
    edges = [(VerilogCircuit, VerilatorCppCircuit)]

    alternative_options = [
        CmdlineOption('--assert'),  # Enable all assertions
        CmdlineOption('--autoflush'),  # Flush the output stream after every $display
        CmdlineOption('--compiler {}', ["clang", "gcc", "msvc"]),  # Enables workarounds for the specified C++ compiler
        CmdlineOption('--converge-limit {}', range(10, 100,
                                                   10)),  # Iteration before raising converge error (default=100)
        CmdlineOption('--coverage-line'),  # Enables basic block line coverage analysis
        CmdlineOption('--coverage-underscore'),  # Enable coverage of signals that start with an underscore
        CmdlineOption('--coverage-user'),  # Enables adding user-inserted functional coverage
        CmdlineOption('--debug-check'),  # Enable internal debugging assertion checks
        CmdlineOption('--no-debug-leak'),  # Free AstNode instances
        CmdlineOption('--debugi {}', range(1, 11)),  # Set the internal debugging level globally (1-10)
        CmdlineOption('--no-decoration'),  # Minimize comments, white space, symbol names, and other decorative items
        CmdlineOption('--dump-defines'),  # Print a list of all defines (with -E)
        CmdlineOption('--dump-dfg'),  # Enable dumping DfgGraph .dot debug files with dumping level 3
        CmdlineOption('--dump-graph'),  # Enable dumping V3Graph .dot debug files with dumping level 3
        CmdlineOption('--dump-tree'),  # Enable dumping Ast .tree debug files with dumping level 3
        CmdlineOption('--dump-tree-dot'),  # Enable dumping Ast .tree.dot debug files in Graphviz Dot format
        CmdlineOption('--dump-tree-addrids'),  # Replace AST node addresses with short identifiers in tree dumps
        CmdlineOption('--dumpi-dfg {}', range(1, 11)),  # Set the internal DfgGraph dumping level globally
        CmdlineOption('--dumpi-graph {}', range(1, 11)),  # Set internal V3Graph dumping level globally
        CmdlineOption('--dumpi-tree {}', range(1, 11)),  # Set internal Ast dumping level globally
        CmdlineOption('--error-limit {}', range(10, 50, 10)),  # Exit after this number of errors (default=50)
        CmdlineOption('--flatten'),  # Flatten the design’s hierarchy
        CmdlineOption('-fno-acyc-simp'),
        CmdlineOption('-fno-assemble'),
        CmdlineOption('-fno-case'),
        CmdlineOption('-fno-combine'),
        CmdlineOption('-fno-const'),
        CmdlineOption('-fno-const-bit-op-tree'),
        CmdlineOption('-fno-dedup'),
        CmdlineOption('-fno-dfg'),
        CmdlineOption('-fno-dfg-peephole'),
        CmdlineOption('-fno-dfg-pre-inline'),
        CmdlineOption('-fno-dfg-post-inline'),
        CmdlineOption('-fno-expand'),
        CmdlineOption('-fno-gate'),
        CmdlineOption('-fno-life'),
        CmdlineOption('-fno-life-post'),
        CmdlineOption('-fno-localize'),
        CmdlineOption('-fno-merge-cond'),
        CmdlineOption('-fno-merge-cond-motion'),
        CmdlineOption('-fno-merge-const-pool'),
        CmdlineOption('-fno-reloop'),
        CmdlineOption('-fno-reorder'),
        CmdlineOption('-fno-split'),
        CmdlineOption('-fno-subst'),
        CmdlineOption('-fno-subst-const'),
        CmdlineOption('-fno-table'),
        CmdlineOption('--gate-stmts {}',
                      range(10, 50, 10)),  # Set the maximum number of statements present in an equation to be inlined
        CmdlineOption('--hierarchical'),  # Enable hierarchical Verilation
        CmdlineOption('--if-depth {}', range(10, 50, 10)),  # Set the depth for the IFDEPTH warning (default=0)
        CmdlineOption('--inline-mult {}', range(100, 2000, 100)),  # Tune the inlining of modules (default=2000)
        CmdlineOption('--instr-count-dpi {}',
                      range(10, 200,
                            10)),  # Tune the assumed dynamic instruction count of the average DPI import (default=200)
        CmdlineOption('-j {}', range(1, 9)),  # Specify the level of parallelism (--build-jobs and --verilate-jobs)
        CmdlineOption('--MMD'),  # Enable the creation of .d dependency files
        CmdlineOption('--no-MMD'),  # Disable the creation of .d dependency files
        CmdlineOption('--MP'),  # When creating .d dependency files with --MMD option, make phony targets
        CmdlineOption('-O3'),  # Enables slow optimizations for the code Verilator itself generates
        CmdlineOption('--output-split {}', range(1000, 20000,
                                                 1000)),  # Enables splitting the .cpp files (default=20000)
        CmdlineOption('-P'),  # Disable generation of `line markers and blank lines (with -E)
        CmdlineOption('--pp-comments'),  # Show comments in preprocessor output (with -E)
        CmdlineOption('--prof-c'),  # Enable the compiler’s profiling flag
        CmdlineOption('--prof-cfuncs'),  # Modify the created C++ functions to support profiling
        CmdlineOption('--prof-exec'),  # Enable collection of execution trace
        CmdlineOption('--reloop-limit {}',
                      range(10, 200,
                            10)),  # The minimum number of iterations the resulting loop needs to have (default=40)
        CmdlineOption('--savable'),  # Enable including save and restore functions in the generated model
        CmdlineOption('--skip-identical'),  # Skip execution of Verilator if all source files are not updated
        CmdlineOption('--no-skip-identical'),  # Disables skipping execution of Verilator
        CmdlineOption('--stats'),  # Creates a dump file with statistics on the design
        CmdlineOption('--stats-vars'),  # Creates more detailed statistics
        CmdlineOption('--trace'),  # Adds waveform tracing code to the model using VCD format
        CmdlineOption('--trace-coverage'),  # Enable tracing to include a signal for every coverage point
        CmdlineOption('--trace-depth {}', range(1, 11)),  # Specify the number of levels deep to enable tracing
        CmdlineOption('--trace-max-array {}', range(8, 32, 8)),  # The maximum array depth of a signal (default=32)
        CmdlineOption('--trace-max-width {}', range(16, 256, 16)),  # The maximum bit width of a signal (default=256)
        CmdlineOption('--no-trace-params'),  # Disable tracing of parameters
        CmdlineOption(
            '--trace-structs'),  # Enable tracing to show the name of packed structure, union, and packed array fields
        CmdlineOption('--trace-underscore'),  # Enable tracing of signals or modules that start with an underscore
        CmdlineOption('--unroll-count {}', range(1, 50)),  # The maximum number of loop iterations that may be unrolled
        CmdlineOption('--unroll-stmts {}', range(1, 50)),  # The maximum number of statements in a loop to be unrolled
        CmdlineOption('--vpi')  # Enable the use of VPI
    ]

    def translate(self, circuit: VerilogCircuit) -> VerilatorCppCircuit:
        workspace = get_workspace()
        cpp_model = ModelTreeView.from_module_decl(circuit.model)
        top_module = cpp_model.top_module()

        #
        # Transforming data
        #

        # create a directory for cpp files
        obj_dir = workspace.path_to_temp_dir('verilator').as_posix()

        # run verilator
        verilog_file = workspace.save_to_file(circuit.data, 'verilator_input.v')
        verilator_elaborate(workspace.context, top_module, verilog_file.as_posix(), obj_dir, self.policy['extra_args'])

        # make cpp files
        # extract bitcode (.bc) and llvm assembly (.ll) files
        escaped_top_module = VerilatorCppCrossbar.escape_name(top_module)
        verilator_compile(workspace.context, escaped_top_module, obj_dir)

        #
        # Transforming model
        #

        self._fulfill_model(escaped_top_module, cpp_model, obj_dir)

        return VerilatorCppCircuit(obj_dir, cpp_model)

    def _fulfill_model(self, escaped_top_module: str, model: ModelTreeView, target_dir: str):
        """Gather information about the variables defined by Verilator to simulate the circuit,
        including their names, offsets, and sizes."""

        VariableInfo = namedtuple('VariableInfo', ['bytes', 'offset'])

        def parse_cpp_main(main_file: TextIOWrapper):
            """Find symbolic variables in the C++ main() function."""

            result = list()

            for line in main_file:
                if m := VERILATOR_VAR_DEF.match(line):
                    result.append(m.group('name'))

            return result

        def parse_ll(ll_file: TextIOWrapper, variables: list[str]):
            """Find offset + size of given variables."""

            base_offset = None
            members = dict()

            enable_collecting = False

            for line in ll_file:

                if m := LL_FILENAME.match(line):
                    # NOTE: Use 'filename' to distinguish the duplicate variables
                    if m.group('top_module') == escaped_top_module:
                        enable_collecting = True

                elif m := LL_DEBUG_INFO.match(line):
                    name = m.group('name')
                    size = int(m.group('size')) >> 3  # in byte
                    offset = int(m.group('offset') or 0) >> 3  # in byte

                    # NOTE: Class members don't always appear right after the class in the debug info of `.ll` file.
                    if m.group('align') and name == 'TOP':
                        base_offset = offset  # offset of "TOP"

                    # NOTE: Aggregate types such as VlWide, VlUnpacked in Verilator has only one debug info entry.
                    if enable_collecting and name in variables and name not in members:
                        members[name] = VariableInfo(size, offset)

            if not base_offset:
                raise RuntimeError('offset of TOP not found')

            # Add base_offset to the local offset
            return {n: VariableInfo(vi.bytes, vi.offset + base_offset) for n, vi in members.items()}

        # function body

        sim_main_file = f'{target_dir}/V{escaped_top_module}__main.cpp'
        ll_file = f'{target_dir}/V{escaped_top_module}.ll'

        with open(sim_main_file, 'r') as fp:
            symbolic_vars = parse_cpp_main(fp)

        # get offset and size
        with open(ll_file, 'r') as fp:
            for name, info in parse_ll(fp, symbolic_vars).items():
                crossbar = VerilatorCppCrossbar.from_data(name, model)
                for path in crossbar.to_model():
                    model.instantiate_item(path, bytes=info.bytes, offset=info.offset)
