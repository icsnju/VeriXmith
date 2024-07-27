from pathlib import Path

from invoke.exceptions import UnexpectedExit

from core.mutators.mutator import MutationError, MutationOperator
from core.thirdparty import yosys_mutate, yosys_synthesis
from core.workspace import get_workspace


class YosysMutate(MutationOperator):

    def __init__(self, N: int) -> None:
        self.N = N

    def apply(self, op: Path) -> None:
        mutation_file_path = op.parent / 'mutations.ys'
        try:
            mutated = yosys_mutate(get_workspace().context, op.as_posix(), mutation_file_path.as_posix(), self.N)
        except UnexpectedExit:
            raise MutationError('internal fault in yosys_mutate()')
        op.write_text(mutated)


class YosysSynthesisAsMutation(MutationOperator):

    def __init__(self, extra_args: list[str]) -> None:
        self.extra_args = extra_args

    def apply(self, op: Path) -> None:
        try:
            synthesized_verilog = yosys_synthesis(get_workspace().context, op.as_posix(), self.extra_args)
        except UnexpectedExit:
            raise MutationError('internal fault in yosys_synthesis()')
        op.write_text(synthesized_verilog)
