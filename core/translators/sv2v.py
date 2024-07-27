from core.circuits.systemverilog import SystemVerilogCircuit
from core.circuits.verilog import VerilogCircuit
from core.thirdparty import zachjs_sv2v
from core.translators.translator import CmdlineOption, MetaTranslator
from core.workspace import get_workspace


class SystemVerilogToVerilog(MetaTranslator):
    edges = [(SystemVerilogCircuit, VerilogCircuit)]

    alternative_options = [
        CmdlineOption('--siloed'),  # Lex input files separately
        CmdlineOption('--verbose'),  # Retain certain conversion artifacts
    ]

    def translate(self, circuit: SystemVerilogCircuit) -> VerilogCircuit:
        workspace = get_workspace()
        sv_file = workspace.save_to_file(circuit.data, 'sv2v_input.sv')
        verilog_content = zachjs_sv2v(workspace.context, sv_file.as_posix(), self.policy['extra_args'])
        verilog_file = workspace.save_to_file(verilog_content, 'sv2v_output.v')
        return VerilogCircuit.from_file(verilog_file)
