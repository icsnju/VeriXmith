from pathlib import Path
from typing import Iterable

from core.thirdparty import semantic_check
from core.workspace import get_workspace


class MutationOperator:

    @staticmethod
    def validate(src: bytes, suffix: str) -> bool:
        """Validate the given Verilog/SystemVerilog source code."""
        workspace = get_workspace()
        filepath = workspace.save_to_file(src, 'mutant' + suffix)
        validity = semantic_check(workspace.context, filepath.as_posix())
        filepath.unlink()
        return validity

    def __init__(self) -> None:
        self.has_error = False

    def generate(self, seed_path: Path, number: int) -> Iterable[bytes]:
        raise NotImplementedError


class MutationError(Exception):
    pass
