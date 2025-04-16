import traceback
from collections import defaultdict, namedtuple
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from itertools import count, product
from pathlib import Path
from pprint import pformat
from random import sample

import jsonpickle
import pysmt.environment
from tqdm import tqdm

import core.translators.klee
import core.translators.verilator
import core.translators.yosys
from core.circuits.circuit import Circuit
from core.circuits.smt import SmtCircuit
from core.circuits.systemverilog import SystemVerilogCircuit
from core.circuits.verilog import VerilogCircuit
from core.consts import DIFFERENCE_FILENAME, EXCEPTION_FILENAME, INPUT_FILENAME, STRATEGY_FILENAME
from core.mutators.heuristics import HeuristicMutator
from core.translators.translator import Conversion
from core.workspace import Workspace
from core.world import WorldMap


def set_result_dir(result_dir: Path):
    assert result_dir.is_dir() and result_dir.exists()
    Workspace.result_dir = result_dir


def replay(hdl_file: Path, json_file: Path) -> None:
    strategy = jsonpickle.decode(json_file.read_text())

    if isinstance(strategy, Conversion):
        conversions = (strategy, )
    else:
        conversions = strategy
    equivalence_check(hdl_file, conversions, test_only=False)


def regression_test(regression_input_dir: Path, n_jobs: int, input_suffix='.v'):
    with ProcessPoolExecutor(max_workers=n_jobs) as executor:
        futures = list()

        for index in count(1):
            input_file = regression_input_dir / (f'input{index:0>6}' + input_suffix)
            strategy_file = regression_input_dir / f'strategy{index:0>6}.json'
            if not (input_file.exists() and strategy_file.exists()):
                break
            futures.append(executor.submit(replay, input_file, strategy_file))

        for _ in tqdm(as_completed(futures), desc='Regression progress', total=len(futures)):
            pass


ValidationGroup = namedtuple('ValidationGroup', ['input_program', 'conversions'])


def sample_compilation_space(rtl_dir: Path,
                             source_type: str,
                             sink_type: str,
                             n_samples: int,
                             max_op: int = 2) -> Iterable[ValidationGroup]:
    """Search for HDL files fitting the given source_type. Create jobs according to the sink_type."""

    # NOTE: Remove the paths from SystemVerilogCircuit including both "sv2v" and "Synlig" edges
    if source_type == 'SystemVerilogCircuit':
        if sink_type == 'VerilogCircuit':
            import core.translators.surelog
            import core.translators.sv2v
        else:
            import core.translators.sv2v

    source_type = eval(source_type)
    sink_type = eval(sink_type)

    if not source_type in (VerilogCircuit, SystemVerilogCircuit):
        raise ValueError(f'invalid source type: {source_type}')

    conversions = [
        Conversion(*subpath) for path in WorldMap.travel(source_type, sink_type)
        for subpath in product(*(translator.all_instances(max_op) for translator in path))
    ]
    for rtl_file in rtl_dir.glob(f'**/*{source_type.FILENAME_EXTENSION}'):
        yield ValidationGroup(rtl_file, tuple(sample(conversions, k=n_samples)))


def convert(input_program: Path, conversion: Conversion) -> Circuit | None:
    """Perform given conversion on the input program.

    When any exception occurs, save the following files to result_dir/compilation/.
    1. Input program.
    2. Conversion in JSON format.
    3. Stack trace and program output (if any)."""

    def create_circuit(rtl_file: Path) -> VerilogCircuit | SystemVerilogCircuit:
        """Create a new Circuit object from given file.

        The method used here to create the source circuit is decided by the suffix of `rtl_file`.
        - .v:  VerilogCircuit.from_file()
        - .sv: SystemVerilogCircuit.from_file()"""

        suffix = rtl_file.suffix
        if suffix == VerilogCircuit.FILENAME_EXTENSION:
            return VerilogCircuit.from_file(rtl_file)
        elif suffix == SystemVerilogCircuit.FILENAME_EXTENSION:
            return SystemVerilogCircuit.from_file(rtl_file)
        else:
            raise ValueError(f'unsupported type of input: "{suffix}"')

    with Workspace() as workspace:
        try:
            # Backup source & conversion files
            workspace.save_to_file(jsonpickle.encode(conversion), STRATEGY_FILENAME)
            input_rtl = workspace.save_to_file(input_program.read_text(), INPUT_FILENAME + input_program.suffix)
            # Create the Circuit object
            source_circuit = create_circuit(input_rtl)
            # Get the target circuit representation through given conversion
            return conversion.apply_to(source_circuit)

        except Exception:
            # Save exception information
            workspace.save_to_file(traceback.format_exc(), EXCEPTION_FILENAME)
            # Save the entire temporary directory
            workspace.save_as('compilation')


def equivalence_check(input_program: Path, conversions: tuple[Conversion, ...], test_only: bool):

    pysmt.environment.reset_env()
    pysmt.environment.get_env().enable_infix_notation = True

    with Workspace() as workspace:
        valid_conversions = list()
        equivalence_classes = defaultdict(set)

        for conversion in conversions:
            if circuit := convert(input_program, conversion):
                valid_conversions.append(conversion)

                if not equivalence_classes:
                    equivalence_classes[circuit].add(conversion)
                else:
                    for pivot in list(equivalence_classes.keys()):
                        try:
                            is_equivalent = pivot.is_equivalent_to(circuit, quick=test_only, counterexample=False)

                        except Exception:
                            # Save exception information
                            workspace.save_to_file(traceback.format_exc(), EXCEPTION_FILENAME)

                        else:
                            if is_equivalent:
                                equivalence_classes[pivot].add(conversion)
                                break
                    else:  # Not equivalent with existing circuits
                        # NOTE: Exceptions raised in the equivalence check are included
                        equivalence_classes[circuit].add(conversion)

        if len(equivalence_classes) > 1:
            workspace.save_to_file(jsonpickle.encode(valid_conversions), STRATEGY_FILENAME)
            workspace.save_to_file(input_program.read_text(), INPUT_FILENAME + input_program.suffix)
            # head, *tail = equivalence_classes.keys()
            # try:  # Try to generate an counterexample
            #     head.is_equivalent_to(*tail, quick=False, counterexample=True)
            # except Exception:
            #     pass  # Ignore any exception (e.g., NotImplementedError)
            workspace.save_to_file(pformat(equivalence_classes), DIFFERENCE_FILENAME)
            workspace.save_as('cross-checking')


def run_validation(validation_groups: Iterable[ValidationGroup], /, *, test_only: bool, n_jobs: int) -> None:

    with ProcessPoolExecutor(max_workers=n_jobs) as executor:
        futures = [
            executor.submit(equivalence_check, vg.input_program, vg.conversions, test_only) for vg in validation_groups
        ]
        for _ in tqdm(as_completed(futures), desc='Validation progress', total=len(futures)):
            pass


def mutate(seed_path: Path, output_dir: Path, max_cnt: int) -> None:
    """Perform mutations on the seed. Write the output to output_dir."""

    with Workspace() as workspace:
        mutator = HeuristicMutator.default()
        for i, mutant in enumerate(mutator.generate(seed_path, max_cnt)):
            # Write the validated mutated file to output_dir
            (output_dir / f'{seed_path.stem}-mutated-{i}{seed_path.suffix}').write_bytes(mutant)

        if mutator.has_error:  # but recoverable
            workspace.save_as('mutation')


def run_mutation(seed_dir: Path, output_dir: Path, n_times: int, n_jobs: int, debug: bool) -> None:
    if debug:
        for seed_path in (*seed_dir.glob('**/*.v'), *seed_dir.glob('**/*.sv')):
            mutate(seed_path, output_dir, n_times)
    else:
        with ProcessPoolExecutor(max_workers=n_jobs) as executor:
            futures = [
                executor.submit(mutate, seed_path, output_dir, n_times)
                for seed_path in (*seed_dir.glob('**/*.v'), *seed_dir.glob('**/*.sv'))
            ]
            for _ in tqdm(as_completed(futures), desc='Mutation progress', total=len(futures)):
                pass
