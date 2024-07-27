from pathlib import Path

from core.circuits.circuit import Circuit
from core.ir.module import ModuleDeclaration
from core.thirdparty import yosys_equivalence_check
from core.workspace import get_workspace


class VerilogParserError(Exception):
    pass


class VerilogCircuit(Circuit):

    FILENAME_EXTENSION = '.v'

    @classmethod
    def from_file(cls, filepath: Path):
        try:
            top_module = ModuleDeclaration.parse_verilog(filepath.as_posix())
        except Exception:
            raise VerilogParserError
        return cls(filepath.read_text(), top_module)

    def to_file(self, filepath: Path):
        filepath.write_text(self.data)

    def is_equivalent_to(self, *others, **kwargs) -> bool:
        if len(others) != 1:
            raise NotImplementedError
        other = others[0]
        assert isinstance(other, VerilogCircuit)
        workspace = get_workspace()
        this_circ = workspace.save_to_file(self.data, f'{self.model.name}.v')
        that_circ = workspace.save_to_file(other.data, f'{other.model.name}.v')
        return yosys_equivalence_check(get_workspace().context, this_circ.as_posix(), self.model.name,
                                       set(self.model._model_design.keys()), that_circ.as_posix(), other.model.name,
                                       set(other.model._model_design.keys()))


class VerilogNetList(Circuit):
    pass
