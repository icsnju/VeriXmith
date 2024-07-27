import json

from core.consts import REG_DECLARATIONS_IN_MODULE, parser
from core.ir.item import CompoundItem, ModuleItem, PrimitiveItem
from core.thirdparty import verilog_to_json
from core.workspace import get_workspace


def registers_by_module_in(filepath: str) -> dict[str, set[str]]:
    with open(filepath, 'rb') as fp:
        tree = parser.parse(fp.read())
    captures = REG_DECLARATIONS_IN_MODULE.captures(tree.root_node)

    result = dict()
    current_module = None
    for _, name, label in sorted(set((node.start_byte, node.text.decode(), label) for node, label in captures)):
        if label == 'module-id':
            current_module = name
        elif label == 'reg-id':
            result.setdefault(current_module, set()).add(name)
    return result


class ItemNotFoundError(Exception):
    pass


class ModuleDeclaration:
    """A module declared in a Verilog source file, which includes:

    - Name: the name of this module itself (not its instance)
    - Ports: declarations of the module's ports
    - Internals: declarations of the module's internal items
    - Submodules: (dict) name of a submodule instance -> its module's declaration"""

    @classmethod
    def parse_verilog(cls, verilog_filepath: str):
        """In this procedure, `yosys` is first called to produce a JSON output.
        Then the original JSON object will be loaded and inspected.

        A `ModuleDeclaration` object of the top-level module will be returned
        after loading all the module declarations in the given Verilog file
        to `_model_design()` with the information in the JSON object mentioned above."""

        registers_by_module = registers_by_module_in(verilog_filepath)

        def not_hidden(json_object: dict):
            """According to the Yosys Manual:

            The "hide_name" fields are set to 1 when the name of this cell
            or net is automatically created and is likely not of interest for
            a regular user.

            So we safely ignore these cells and nets."""
            return json_object['hide_name'] == 0

        model_design = dict()  # all the modules declared in this circuit

        info = json.loads(verilog_to_json(get_workspace().context, verilog_filepath))

        # Top-level modules are modules that are included in the source text,
        # but do not appear in any module instantiation statement.
        # Therefore, we can automatically detect the top module's name by
        # counting the reference times of each module in the JSON file.
        # (In fact, counting the number of the modules once appeared as a
        # submodule would be enough.)
        # If the number of top-level modules > 1, reject the input design.
        non_top_modules = set()

        for module_name, module_details in info['modules'].items():

            if 'memories' in module_details.keys():
                raise NotImplementedError('"memories" is not supported')

            # input/output/inout ports
            ports = {
                # NOTE: ports can only be declared as primitive items
                PrimitiveItem(port_name, len(port_details['bits']), False, port_details['direction'])
                for port_name, port_details in module_details['ports'].items()
            }
            register_names = registers_by_module.get(module_name, set())

            # used to exclude ports from internal nets later
            port_names = module_details['ports'].keys()

            # internal variables (reg / wire) excluding ports
            # can be wide or array object
            internals = set()
            compound_items_in_module = dict()
            for net_name, net_details in module_details['netnames'].items():
                if not_hidden(net_details) and net_name not in port_names:
                    if m := CompoundItem.ARRAY_ELEMENT.match(net_name):
                        arr_name, index = m.group('name', 'index')
                        array_item = CompoundItem.register_element(compound_items_in_module, module_name, arr_name,
                                                                   arr_name in register_names, int(index),
                                                                   len(net_details['bits']))
                        internals.add(array_item)  # no effect if the item is already present
                    else:
                        internals.add(PrimitiveItem(net_name, len(net_details['bits']), net_name in register_names))

            # sub-module instances
            submodules = dict()
            for cell_name, cell_details in module_details['cells'].items():
                if not_hidden(cell_details):
                    submodule_type = cell_details['type']
                    submodules[cell_name] = submodule_type
                    # label this submodule_type as non-top
                    non_top_modules.add(submodule_type)

            # Register a new module declaration from the Verilog source file.
            model_design[module_name] = cls(module_name, ports, internals, submodules, model_design)

        # find out the module(s) with reference count 0
        top_modules = model_design.keys() - non_top_modules
        if len(top_modules) != 1:
            raise ValueError(f'multiple top-level modules found ({", ".join(top_modules)})')

        return model_design.get(top_modules.pop())  # return the top-level module's declaration

    def __init__(self, name: str, ports: set, internals: set, submodules: dict, model_design: dict) -> None:
        self._name = name
        self._ports = {p.name: p for p in ports}
        self._internals = {i.name: i for i in internals}
        self._submodules = submodules
        self._model_design = model_design

    @property
    def name(self):
        return self._name

    @property
    def ports(self):
        return self._ports

    @property
    def internals(self):
        return self._internals

    @property
    def submodules(self):
        return {k: self._model_design.get(v) for k, v in self._submodules.items()}

    def new_instance(self):
        """Create a new model instance of this type."""
        return ModuleInstance(self)


class ModuleInstance:
    """Conceptually stands for an instance of a module.
    It has a reference to its declaration, along with the real objects implemented by the circuit.

    This is because for different types of circuits (defined in `core.circuits`),
    module instances may be implemented in different ways and thus have different attributes."""

    def __init__(self, module_decl: ModuleDeclaration):
        self._declaration = module_decl
        self._port_instances = dict()
        self._internal_instances = dict()

    @property
    def module_type(self):
        return self._declaration.name

    def find_instance(self, name: str) -> ModuleItem:
        """Search for the name in all items in this module instance.

        Used when constructing data form in the crossbars.
        A missing instance leads to a KeyError."""

        if name in self._internal_instances:
            return self._internal_instances[name]
        elif name in self._port_instances:  # try finding given name in ports
            return self._port_instances[name]
        else:
            raise ItemNotFoundError(self.find_decl(name))

    def find_decl(self, name: str) -> ModuleItem | None:
        """Search for the declaration of the name.

        Used to find corresponding declarations before turing them into instances.
        It is OK that a declaration is not found. In this case, `None` will be returned."""

        if name in self._declaration.internals:
            return self._declaration.internals[name]
        else:  # try finding given name in ports
            return self._declaration.ports.get(name)

    def input_ports(self):
        """Iterate over the instantiated input ports. Yield (name, instance) tuples."""
        for name, instance in self._port_instances.items():
            if instance.is_input_port():
                yield name, instance

    def output_ports(self):
        """Iterate over the instantiated output ports. Yield (name, instance) tuples."""
        for name, instance in self._port_instances.items():
            if instance.is_output_port():
                yield name, instance

    def internals(self):
        """Iterate over the instantiated internal items. Yield (name, instance) tuples."""
        return self._internal_instances.items()

    def submodules(self):
        """Iterate over the submodules. Yield (instance name, module declaration) tuples."""
        return self._declaration.submodules.items()

    def instantiate_item(self, item_name, **attrs):
        """Instantiate the specified item then attach the given attributes."""

        if decl := self.find_decl(item_name):
            if decl.is_port():
                self._port_instances[item_name] = decl.new_instance(**attrs)
            elif item_name not in self._internal_instances:
                self._internal_instances[item_name] = decl.new_instance(**attrs)
            elif not isinstance(decl, CompoundItem):
                raise ValueError(f'duplicate instantiation of {item_name}')
        else:
            raise ValueError(f"'{item_name}' not found in module '{self._declaration.name}'")
