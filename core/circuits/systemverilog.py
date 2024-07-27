from pathlib import Path

from core.circuits.circuit import Circuit


class SystemVerilogCircuit(Circuit):

    FILENAME_EXTENSION = '.sv'

    @classmethod
    def from_file(cls, filepath: Path):
        return cls(filepath.read_text(), None)

    def to_file(self, filepath: Path):
        filepath.write_text(self.data)
