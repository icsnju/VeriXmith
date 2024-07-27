from typing import Iterable

from core.circuits.circuit import Circuit
from core.ir.crossbar import KleeSmtCrossbar
from core.ir.item import ModuleItem
from core.ir.view import HierarchicalPathName


class CppCircuit(Circuit):

    def atom_variables(self, source: Iterable[tuple[HierarchicalPathName, ModuleItem]]):
        """Returns (name, offset, bytes) tuples of all the symbolic variables in given source,
        which are in ascending order of the `offset` field."""
        raise NotImplementedError


class VerilatorCppCircuit(CppCircuit):

    def atom_variables(self, source: Iterable[tuple[HierarchicalPathName, ModuleItem]]):
        crossbar = KleeSmtCrossbar.from_model(*(p for p, _ in source))
        return sorted(crossbar.to_data(self.model, split=True), key=lambda x: x.offset)


class YosysCppCircuit(CppCircuit):

    def atom_variables(self, source: Iterable[tuple[HierarchicalPathName, ModuleItem]]):
        crossbar = KleeSmtCrossbar.from_model(*(p for p, _ in source))
        return sorted(crossbar.to_data(self.model, split=True), key=lambda x: x.offset)
