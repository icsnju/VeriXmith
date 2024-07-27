from collections import namedtuple
from functools import reduce
from typing import Callable, Iterable

from pysmt.fnode import FNode
from pysmt.shortcuts import BVConcat, BVOne, BVZero, Ite, Symbol
from pysmt.typing import BVType, FunctionType, Type

from core.ir.item import CompoundItem, ModuleItem, PrimitiveItem
from core.ir.view import HierarchicalPathName, ModelTreeView


# convert boolean to bit-vector
def bool_to_bv(x: FNode) -> FNode:
    try:
        x.bv_width()
    except (AssertionError, AttributeError):  # x is not a bit-vector
        return Ite(x, BVOne(1), BVZero(1))
    else:
        return x


class Crossbar:

    def __init__(self, *paths):
        """Item references are saved as paths. A crossbar can contain one or more items."""
        self._paths = paths

    @classmethod
    def from_data(cls, name, model):
        """Create a crossbar for items in the `model` represented by `name`."""
        tokens = cls._tokenize(name)
        paths = cls._parse(tokens, model)
        return cls(*paths)

    @classmethod
    def from_model(cls, *paths):
        """Create a crossbar for items in the `model` represented by `paths`."""
        return cls(*paths)

    def to_data(self, model):
        """Get the items in the form of data."""
        raise NotImplementedError

    def to_model(self):
        """Get the items in the form of model (internal representation)."""
        return self._paths

    @staticmethod
    def _tokenize(name):
        raise NotImplementedError

    @staticmethod
    def _parse(tokens, model):
        raise NotImplementedError


class YosysSmtCrossbar(Crossbar):

    @staticmethod
    def _tokenize(name):
        return name

    @staticmethod
    def _parse(tokens, model):
        """Find out all the paths containing a sub-path represented by tokens."""

        assert len(tokens) == 2
        module_type, item_name = tokens

        # map all array elements into one CompoundItem
        if m := CompoundItem.ARRAY_ELEMENT.match(item_name):
            item_name = m.group('name')

        for node in model.filter_nodes(lambda n: n.data.module_type == module_type):
            if node.data.find_decl(item_name):
                yield HierarchicalPathName(node.identifier, item_name)

    def to_data(self, model: ModelTreeView, function_definitions: dict[str, Callable[[FNode], FNode]]):
        for p in self._paths:
            nodes = model[p]
            module_inst = nodes[-1].data

            # Find out the function chain leads to the module instance
            # a.k.a. |<type>_h <instance>|*
            hierarchy_functions = [
                function_definitions[f'{prev.data.module_type}_h {curr.tag}']
                for prev, curr in zip(nodes[:-1], nodes[1:])
            ]

            # Find the item in leaf module
            # |<type>_n <wirename>|
            item_inst = module_inst.find_instance(p.item_name)
            item_names = list()
            if isinstance(item_inst, CompoundItem):
                item_names.extend((f'{item_inst.name}[{i}]' for i in range(item_inst.capacity)))
            else:
                item_names.append(item_inst.name)

            sub_functions = [function_definitions[f'{module_inst.module_type}_n {n}'] for n in item_names]

            def accessor_maker(function: Callable[[FNode], FNode]) -> Callable[[FNode], FNode]:

                def accessor(state: FNode) -> FNode:
                    result = state
                    for f in hierarchy_functions:
                        result = f(result)

                    return bool_to_bv(function(result))

                return accessor

            for f in sub_functions:
                yield accessor_maker(f)


class VerilatorNamingHelper:

    DOT = '__DOT__'

    # NOTE: Arrayed instances are named `{instanceName}[{instanceNumber}]` in Verilog,
    # which becomes `{instanceName}__BRA__{instanceNumber}__KET__` inside the generated C++ code.
    LPAREN = '__BRA__'
    RPAREN = '__KET__'

    DOLLAR = '__024'

    @staticmethod
    def split(full_name: str) -> Iterable[str]:
        full_name = full_name.replace(VerilatorNamingHelper.DOLLAR, '$')
        if VerilatorNamingHelper.DOT not in full_name:
            # input/output port of the top module
            return (full_name, )
        else:
            name = full_name.replace(VerilatorNamingHelper.LPAREN, '[').replace(VerilatorNamingHelper.RPAREN, ']')
            return name.split(VerilatorNamingHelper.DOT)

    @staticmethod
    def merge(submodules: list[str], item: str, is_port: bool) -> str:
        if len(submodules) == 1 and is_port:
            # port of the top module
            return item.replace('$', VerilatorNamingHelper.DOLLAR)
        else:
            submodules.append(item)
            var_name = VerilatorNamingHelper.DOT.join(submodules)
            var_name = var_name.replace('[', VerilatorNamingHelper.LPAREN)
            var_name = var_name.replace(']', VerilatorNamingHelper.RPAREN)
            return var_name.replace('$', VerilatorNamingHelper.DOLLAR)

    @staticmethod
    def find(path: HierarchicalPathName, model: ModelTreeView) -> tuple[str, ModuleItem]:
        nodes = model[path]
        tags = [n.tag for n in nodes]

        module_inst = nodes[-1].data
        item_inst = module_inst.find_instance(path.item_name)

        return VerilatorNamingHelper.merge(tags, item_inst.name, item_inst.is_port()), item_inst


class KleeSmtCrossbar(Crossbar):
    accessor_name = lambda prefix, name: f'{prefix}__{name}'

    AtomVariable = namedtuple('AtomVariable', ['name', 'offset', 'bytes'])

    @staticmethod
    def _make_function(fn_name: str, rv_width: int):
        return Symbol(fn_name, FunctionType(BVType(rv_width), [Type('Klee-State')]))

    def to_data(self, model, split=False) -> Iterable[AtomVariable | Callable[[FNode], FNode]]:
        """Returns the SMT-LIBv2 formulas corresponding to paths (one path, one formula).

        If `split=True`, yields (name, offset, bytes) information of every primitive item."""

        for p in self._paths:
            # first find the Verilator variable name
            var_name, item_inst = VerilatorNamingHelper.find(p, model)

            # add suffix to `var_name` if needed
            atom_variables = list()

            if isinstance(item_inst, CompoundItem):
                element_bytes = item_inst.bytes // item_inst.capacity

                # (0, 0, 0, ...) -> (x-1, y-1, z-1, ...)
                for element_index in range(item_inst.capacity):

                    element_offset = item_inst.offset + element_index * element_bytes

                    if element_bytes < 8:
                        atom_variables.append(
                            KleeSmtCrossbar.AtomVariable(name=f'{var_name}_{element_index}',
                                                         offset=element_offset,
                                                         bytes=element_bytes))
                    else:  # handle wide variables here
                        words = element_bytes // 4
                        for i in range(words):
                            atom_variables.append(
                                KleeSmtCrossbar.AtomVariable(name=f'{var_name}_{element_index * words + i}',
                                                             offset=element_offset + i * 4,
                                                             bytes=4))

            elif item_inst.is_wide():  # primitive wide item
                for i in range(item_inst.bytes // 4):  # floor division
                    atom_variables.append(
                        KleeSmtCrossbar.AtomVariable(name=f'{var_name}_{i}', offset=(item_inst.offset + i * 4),
                                                     bytes=4))
            else:
                atom_variables.append(
                    KleeSmtCrossbar.AtomVariable(name=var_name, offset=item_inst.offset, bytes=item_inst.bytes))

            if split:
                yield from atom_variables
            else:
                # construct the name of accessor function in PySMT
                sub_functions = [
                    KleeSmtCrossbar._make_function(fn_name=KleeSmtCrossbar.accessor_name(model.top_module(), v.name),
                                                   rv_width=(v.bytes << 3)) for v in atom_variables
                ]

                # NOTE: under two circumstances we concat sub-functions:
                # 1. one single wide primitive item
                # 2. one compound item contains several wide elements
                is_wide_primitive = isinstance(item_inst, PrimitiveItem) and len(sub_functions) > 1
                is_wide_array = isinstance(item_inst, CompoundItem) and len(sub_functions) > item_inst.capacity

                def accessor_maker(words: list[Callable[[FNode], FNode]]):

                    def accessor(state: FNode):
                        # order in sub_functions: 0 (lsb) ... n (msb)
                        # to restore the original bit-vector, concat from msb to lsb, so reversed is needed here
                        return reduce(BVConcat, map(lambda f: f(state), reversed(words)))

                    return accessor

                if is_wide_primitive:
                    yield accessor_maker(sub_functions)

                elif is_wide_array:
                    for start in range(0, len(sub_functions), item_inst.capacity):
                        func_group = sub_functions[start:start + item_inst.capacity]
                        yield accessor_maker(func_group)

                else:
                    for function in sub_functions:
                        yield (lambda state: function(state))


class VerilatorCppCrossbar(Crossbar):

    @staticmethod
    def escape_name(name: str) -> str:
        return name.replace('$', VerilatorNamingHelper.DOLLAR)

    @staticmethod
    def _tokenize(name: str):
        return VerilatorNamingHelper.split(name)

    @staticmethod
    def _parse(tokens, model):
        if len(tokens) == 1:  # port of the top module
            tokens = (model.top_module(), *tokens)
        # NOTE: In Verilator, an array is represented by one single object, which occurs only once.
        # Besides, it directly matches the name of the leaf node.
        yield from model.match_path(tokens)


class YosysCxxCrossbar(VerilatorCppCrossbar):

    DebugItem = namedtuple('DebugItem', ['name', 'width', 'writable_and_non_output'])

    CxxImplItem = namedtuple('CxxImplItem', ['origin_name', 'cxx_name', 'array_size', 'bit_width', 'is_symbolic'])

    @staticmethod
    def mangle_name(name: str) -> str:
        """This function behaves as `mangle_name()` in `backends/cxxrtl/cxxrtl_backend.cc` of the Yosys project.
        Although we access variables with the interface provided by `debug_info()`, module names are influenced
        by name mangling. We need to modify `model.top_module` to match the class name generated by CXXRTL.

        NOTE: Only simple identifiers are considered here."""

        return name.replace('_', '__').replace('$', '_24_')

    @staticmethod
    def preprocess(debug_items: list[DebugItem], model: ModelTreeView) -> list[CxxImplItem]:
        """Turns the given name (in Yosys naming style) into Verilator naming style.
        Returns `CxxImplItem`s."""

        result = list()
        seen = set()

        for debug_item in debug_items:

            # NOTE: In Yosys-CXXRTL arrays, each element is represented by one object.
            # For example, `arr[0]`~`arr[k-1]` stand for the k elements in a CompoundItem `arr`.
            # In this situation, the last token should be checked.
            # We need to remove the index in the last token (if exists) so that
            # its name matches a CompoundItem's name.
            *submodules, item_name = debug_item.name.split(' ')
            if m := CompoundItem.ARRAY_ELEMENT.match(item_name):
                item_name = m.group('name')
            origin_name = ' '.join((*submodules, item_name))
            # Avoid duplicate entries of one CompoundItem
            # NOTE: After one of the elements in a CompoundItem is processed,
            # the following elements from the same CompoundItem will be skipped.
            # This means that the attributes in CxxImplItem, especially "is_symbolic", are expected to be the same
            # for all elements in one array. This could lead to hard-to-debug differences in the future.
            if origin_name in seen:
                continue
            seen.add(origin_name)

            # NOTE: In Verilator, all the variable names start by the top module's name
            # except the ports of the top module.
            # In Yosys-CXXRTL, there is no such prefix.
            paths = list(model.match_path((model.top_module(), *submodules, item_name)))
            if (path_cnt := len(paths)) != 1:
                raise ValueError(f'"{model.top_module()} {origin_name}" matches {path_cnt} paths (expected 1)')
            path = paths.pop()
            # Find the item_decl in the model
            nodes = model[path]
            tags = [n.tag for n in nodes]  # used to construct a name in Verilator naming style later

            module_inst = nodes[-1].data
            item_decl = module_inst.find_decl(path.item_name)

            result.append(
                YosysCxxCrossbar.CxxImplItem(
                    origin_name=origin_name,
                    cxx_name=VerilatorNamingHelper.merge(tags, item_decl.name, item_decl.is_port()),
                    array_size=(item_decl.capacity if isinstance(item_decl, CompoundItem) else 1),
                    bit_width=debug_item.width,
                    is_symbolic=(debug_item.writable_and_non_output
                                 and (item_decl.is_register() or item_decl.is_port()))))

        return result
