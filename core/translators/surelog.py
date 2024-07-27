from core.circuits.systemverilog import SystemVerilogCircuit
from core.circuits.verilog import VerilogCircuit
from core.thirdparty import yosys_systemverilog_plugin
from core.translators.translator import CmdlineOption, MetaTranslator
from core.workspace import get_workspace


class SurelogPlugin(MetaTranslator):
    edges = [(SystemVerilogCircuit, VerilogCircuit)]

    alternative_options = [
        CmdlineOption('-sverilog'),  # Forces all files to be parsed as SystemVerilog files
        CmdlineOption('-fileunit'),  # Compiles each Verilog file as an independent compilation unit
        CmdlineOption('-diffcompunit'),  # Compiles both all files as a whole unit and separate compilation units
        CmdlineOption('-parse'),  # Parse/Compile/Elaborate/Produces UHDM
        CmdlineOption('-noparse'),  # Turns off Parsing & Compilation & Elaboration
        CmdlineOption('-nocomp'),  # Turns off Compilation & Elaboration
        CmdlineOption('-noelab'),  # Turns off Elaboration
        CmdlineOption('-elabuhdm'),  # Forces UHDM/VPI Full Elaboration/Uniquification
        CmdlineOption('-pythonlistener'),  # Enables the Parser Python Listener
        CmdlineOption('-nopython'),  # Turns off all Python features
        CmdlineOption('-withpython'),  # Turns on all Python features
        CmdlineOption('-strictpythoncheck'),  # Turns on strict Python checks
        CmdlineOption('-mt {}', range(8 + 1)),  # 0 up to 512 max threads
        CmdlineOption('-mp {}', range(8 + 1)),  # 0 up to 512 max processes
        CmdlineOption('-split {}', range(10, 500, 50)),  # Split files/modules larger than specified line number
        CmdlineOption('--enable-feature={}', ["parametersubstitution", "letexprsubstitution"]),
        CmdlineOption('--disable-feature={}', ["parametersubstitution", "letexprsubstitution"]),
    ]

    def translate(self, circuit: SystemVerilogCircuit) -> VerilogCircuit:
        workspace = get_workspace()
        sv_file = workspace.save_to_file(circuit.data, 'surelog_input.sv')
        verilog_content = yosys_systemverilog_plugin(workspace.context,
                                                     sv_file.as_posix(),
                                                     extra_args=self.policy['extra_args'])
        verilog_file = workspace.save_to_file(verilog_content, 'surelog_output.v')
        return VerilogCircuit.from_file(verilog_file)
