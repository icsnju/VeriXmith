import textwrap
from itertools import chain, pairwise
from pathlib import Path
from shlex import quote
from typing import Iterable

from invoke.collection import Collection
from invoke.tasks import task

from core.consts import DEBUG_CPP_TEMPLATE, DEFAULT_TIMEOUT, KLEE_TIMEOUT, REG_DECLARATION, SMT_SOLVER_TIMEOUT, parser
from core.ir.crossbar import YosysCxxCrossbar


def _yosys_script_wrapper(script: str):
    return f"yosys -qq -p {quote(script)}"


@task
def yosys_write_smt2(c, verilog_filepath, top_module):
    # Yosys script that produce smt2 output
    script = f"""read_verilog -noassert -mem2reg {verilog_filepath}; hierarchy -check -top {top_module}
        proc; opt; dffunmap; write_smt2 -wires"""
    result = c.run(_yosys_script_wrapper(script), timeout=DEFAULT_TIMEOUT)
    return result.stdout


@task(iterable=['extra_args'])
def yosys_write_cxxrtl(c, verilog_filepath, top_module, output_dir, extra_args):
    # Yosys script that produce C++ output
    script = f"""read_verilog -noassert -mem2reg {verilog_filepath}; hierarchy -check -top {top_module}
        write_cxxrtl {' '.join(extra_args)} {output_dir}/{YosysCxxCrossbar.mangle_name(top_module)}.cpp"""
    c.run(_yosys_script_wrapper(script), timeout=DEFAULT_TIMEOUT)


@task(iterable=['extra_args'])
def yosys_synthesis(c, verilog_filepath, extra_args):
    script = f"""read_verilog -noassert -mem2reg {verilog_filepath}; hierarchy -check -auto-top
        synth -auto-top {' '.join(extra_args)}
        write_verilog -noattr -siminit"""
    result = c.run(_yosys_script_wrapper(script), timeout=DEFAULT_TIMEOUT)
    return result.stdout


@task
def yosys_mutate(c, verilog_filepath, mutation_file_path, N):
    script = f"""read_verilog -noassert -mem2reg {verilog_filepath}; hierarchy -check -auto-top
        mutate -list {N} -o {mutation_file_path}; script {mutation_file_path}; proc
        write_verilog -noattr -siminit"""
    result = c.run(_yosys_script_wrapper(script), timeout=DEFAULT_TIMEOUT)
    return result.stdout


@task(iterable=['extra_args'])
def yosys_systemverilog_plugin(c, systemverilog_file, extra_args):
    script = f"""plugin -i systemverilog; read_systemverilog -nostdout {' '.join(extra_args)} {systemverilog_file}
        proc; opt; write_verilog -noattr -siminit"""
    result = c.run(_yosys_script_wrapper(script), timeout=DEFAULT_TIMEOUT)
    return result.stdout


@task(iterable=['modules_a', 'modules_b'])
def yosys_equivalence_check(c, file_a, top_a, modules_a, file_b, top_b, modules_b):

    def new_name(module_name: str, label: str) -> str:
        return f'_${label}_{module_name}'

    def rename_commands(modules: Iterable[str], label: str) -> str:
        return '; '.join(f'rename {module} {new_name(module, label)}' for module in modules)

    label_a, label_b = 'a', 'b'
    script = f"""read_verilog -noassert -mem2reg {file_a}; {rename_commands(modules_a, label_a)}
    read_verilog -noassert -mem2reg {file_b}; {rename_commands(modules_b, label_b)}
    miter -equiv -make_assert -flatten {new_name(top_a, label_a)} {new_name(top_b, label_b)} miter
    sat -verify -prove-asserts miter"""

    result = c.run(_yosys_script_wrapper(script), timeout=SMT_SOLVER_TIMEOUT)
    return result.exited == 0


@task
def verilog_to_json(c, verilog_filepath):
    script = f"""read_verilog -noassert -mem2reg {verilog_filepath}; hierarchy -check
        proc; write_json"""  # directly write to STDOUT
    result = c.run(_yosys_script_wrapper(script), timeout=DEFAULT_TIMEOUT)
    return result.stdout


@task
def dump_debug_info(c, mangled_top_module, output_dir):
    # Generate the main() function in debug.cpp
    with open(f'{output_dir}/debug.cpp', 'w') as fp:
        fp.write(textwrap.dedent(DEBUG_CPP_TEMPLATE.format(top_module=mangled_top_module)))

    with c.cd(output_dir):
        # Compile & Run
        c.run(
            'wllvm++ -w -o debug debug.cpp -I$(yosys-config --datdir)/include/backends/cxxrtl/runtime $CXXFLAGS $LDFLAGS',
            timeout=DEFAULT_TIMEOUT)
        c.run('./debug', timeout=DEFAULT_TIMEOUT)


@task
def yosys_compile(c, top_module, target_dir):
    with c.cd(target_dir):
        c.run(
            f'wllvm++ -w -o {top_module} main.cpp -g $CXXFLAGS $LDFLAGS'
            ' -I$(yosys-config --datdir)/include/backends/cxxrtl/runtime -lkleeRuntest -L/tmp/klee_build130stp_z3/lib/',
            timeout=DEFAULT_TIMEOUT)
        c.run(f'extract-bc -v {top_module}', timeout=DEFAULT_TIMEOUT)
        c.run(f'llvm-dis {top_module}.bc', timeout=DEFAULT_TIMEOUT)


@task(iterable=['extra_args'])
def verilator_elaborate(c, top_module, verilog_src_file, target_dir, extra_args):
    """Generates C++ source by Verilator. Only supports single input Verilog file."""

    def insert_comments(before, after) -> None:
        """Insert `/*verilator public_flat*/` after each register declaration.
        This is not an atom mutator. It's used for preprocess in VerilatorTransformer."""

        comment = '/*verilator public_flat*/'.encode()

        with open(before, 'rb') as fp:
            before = fp.read()

        captures = REG_DECLARATION.captures(parser.parse(before).root_node)
        insert_points = sorted(node.end_byte for node, _ in captures)

        # Insert all at once
        with open(after, 'wb') as fp:
            fp.write(comment.join(before[i:j] for i, j in pairwise(chain((0, ), insert_points, (len(before), )))))

    origin_verilog = Path(verilog_src_file)
    commented_verilog = origin_verilog.with_stem('commented-' + origin_verilog.stem)
    insert_comments(origin_verilog, commented_verilog)

    # basic options
    command = [
        'verilator', '-cc', '-exe', '-sym-exec-main', '--no-timing', '-Wno-fatal', '-Wno-lint', '-Wno-style',
        '-top-module', top_module, '-Mdir', target_dir, '--waiver-output', f'{target_dir}/warnings.waiver'
    ]

    command.extend(extra_args)

    command.append('"' + commented_verilog.as_posix() + '"')

    # c flags
    command.extend(['-CFLAGS', '"' + ' '.join(['-g', '-O0', '-w']) + '"'])

    # ld flags
    command.extend(['-LDFLAGS', '"-lkleeRuntest -L/tmp/klee_build130stp_z3/lib/"'])

    c.run(' '.join(command), timeout=DEFAULT_TIMEOUT)


@task
def semantic_check(c, src_file):
    cmd = f'iverilog {src_file} && rm a.out' if src_file.endswith('.v') else _yosys_script_wrapper(
        f'plugin -i systemverilog; read_systemverilog -synth {src_file}')
    # NOTE: avoid raising UnexpectedExit when the executed command exits with a nonzero status (warn=True)
    try:
        # NOTE: Setting `warn=True` only prevents raising UnexpectedExit.
        #       Other exceptions, such as ThreadException, will still be raised.
        result = c.run(cmd, timeout=DEFAULT_TIMEOUT)
    except Exception:
        return False
    return result.exited == 0


@task
def verilator_compile(c, top_module, target_dir):
    """Build Verilated C++ class with wllvm++.
    Extract LLVM bitcode file from the executable file.
    Convert LLVM bitcode file to readable assembly code."""

    makefile = f'V{top_module}.mk'

    # NOTE: disable ccache, otherwise wllvm will break down.
    c.run(f'make -C {target_dir} -f {makefile} CXX=wllvm++ LINK=wllvm++',
          env={'CCACHE_DISABLE': '1'},
          timeout=DEFAULT_TIMEOUT)

    with c.cd(target_dir):
        c.run(f'extract-bc -v V{top_module}', timeout=DEFAULT_TIMEOUT)
        c.run(f'llvm-dis V{top_module}.bc', timeout=DEFAULT_TIMEOUT)


@task(iterable=['extra_args'])
def zachjs_sv2v(c, systemverilog_file, extra_args):
    result = c.run(f'sv2v {" ".join(extra_args)} {systemverilog_file}', timeout=DEFAULT_TIMEOUT)
    return result.stdout


@task(iterable=['extra_args'])
def symbolic_execution(c, input_file, output_dir, working_dir, extra_args):
    """Call KLEE to perform symbolic execution.
    NOTE: KLEE must be called under the same directory as the bitcode file."""

    # basic options
    command = [
        'klee', '--posix-runtime', '--libc=uclibc', '--libcxx', '--write-smt2s', '--write-snapshots',
        '--disable-verify', '--check-div-zero=false', '--check-overshift=false', '--warnings-only-to-file'
    ]

    command.extend(extra_args)

    # output path
    command.append(f'--output-dir={output_dir}')

    # input file
    command.append(input_file)

    with c.cd(working_dir):
        c.run(' '.join(command), timeout=KLEE_TIMEOUT)


yosys = Collection('yosys')
yosys.add_task(yosys_write_smt2)
yosys.add_task(yosys_write_cxxrtl)
yosys.add_task(yosys_synthesis)
yosys.add_task(yosys_mutate)
yosys.add_task(yosys_systemverilog_plugin)
yosys.add_task(yosys_equivalence_check)
yosys.add_task(verilog_to_json)
yosys.add_task(dump_debug_info)
yosys.add_task(yosys_compile)

verilator = Collection('verilator')
verilator.add_task(verilator_elaborate)
verilator.add_task(verilator_compile)

miscellaneous = Collection('miscellaneous')
miscellaneous.add_task(zachjs_sv2v)
miscellaneous.add_task(symbolic_execution)
