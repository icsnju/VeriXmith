from collections import namedtuple
from typing import Literal

from treelib import Node, Tree
from treelib.exceptions import NodeIDAbsentError

HierarchicalPathName = namedtuple('HierarchicalPathName', ['module_instance_id', 'item_name'])
"""Specify an element in a circuit."""


class ModelTreeView:
    """Represents a model with a tree. The root of the tree is the top-level
    module. Every node in the tree is a `ModuleInstance`.

    In a `ModelTreeView`, we'll be able to refer to a module instance and
    add new attributes, such as offset, to its ports or internal wires."""

    @classmethod
    def from_module_decl(cls, top_module):
        """Construct a `ModelTreeView` object from the `ModuleDeclaration` and all its submodules recursively.
        In the tree-view of a model, all module instances are arranged in a tree hierarchy."""

        tree = Tree()
        root_node = tree.create_node(tag=top_module.name,
                                     data=top_module.new_instance(),
                                     identifier=hash(top_module.name))

        unvisited_nodes = [root_node]
        while unvisited_nodes:
            parent_node = unvisited_nodes.pop(0)
            # data of the node is a `ModuleInstance` object
            for instance_name, module_decl in parent_node.data.submodules():
                child_node = tree.create_node(tag=instance_name,
                                              data=module_decl.new_instance(),
                                              identifier=hash(
                                                  (module_decl.name, instance_name, parent_node.identifier)),
                                              parent=parent_node.identifier)
                unvisited_nodes.append(child_node)

        return cls(tree)

    def __init__(self, tree: Tree):
        self._tree = tree

    def __getitem__(self, path) -> list[Node]:
        """Get all the nodes on the path (starting from the root)."""
        if not isinstance(path, HierarchicalPathName):
            raise TypeError(f'indices must be HierarchicalPathName, not {type(path)}')
        # verify the model reference in given path == self
        try:
            topdown_nid_list = reversed(list(self._tree.rsearch(path.module_instance_id)))
        except NodeIDAbsentError as err:
            raise ValueError(f'"{path.item_name}" is not a leaf node') from err
        return [self._tree[nid] for nid in topdown_nid_list]

    def _top_node(self) -> Node:
        """Get the root node of the tree."""
        return self._tree[self._tree.root]

    def _input_ports(self):
        """Iterate over the input ports of this model. Yield (path, instance) tuples."""
        for name, instance in self._top_node().data.input_ports():
            yield HierarchicalPathName(self._tree.root, name), instance

    def _output_ports(self):
        """Iterate over the output ports of this model. Yield (path, instance) tuples."""
        for name, instance in self._top_node().data.output_ports():
            yield HierarchicalPathName(self._tree.root, name), instance

    def _internals(self):
        """Iterate over all the internal items of this model. Yield (path, instance) tuples."""
        for nid, node in self._tree.nodes.items():
            module_instance = node.data
            for internal_name, internal_instance in module_instance.internals():
                yield HierarchicalPathName(nid, internal_name), internal_instance

    @property
    def all_items(self):
        """Returns all the nets and registers defined in this circuit."""

        yield from self._input_ports()
        yield from self._output_ports()
        yield from self._internals()

    @property
    def internal_registers(self):
        """The internal states of a circuit."""
        return ((p, i) for p, i in self._internals() if i.is_register())

    @property
    def internal_wires(self):
        yield from ((p, i) for p, i in self._internals() if not i.is_register())

    @property
    def input_ports(self):
        """The input ports of a circuit."""
        yield from self._input_ports()

    @property
    def output_ports(self):
        """The output ports of a circuit."""
        yield from self._output_ports()

    @property
    def combination_inputs(self):
        """The combination inputs of a sequential circuit contains its input
        ports and the output values of internal registers."""

        yield from self._input_ports()
        yield from self.internal_registers

    @property
    def combination_outputs(self):
        """The combination outputs of a sequential circuit contains its output
        ports and the input values of internal registers."""

        yield from self._output_ports()
        yield from self.internal_registers

    VIEWS = Literal[
        'all_items',
        'internal_registers',
        'internal_wires',
        'input_ports',
        'output_ports',
        'combination_inputs',
        'combination_outputs',
    ]

    def top_module(self) -> str:
        """Name of the top-level module."""
        return self._top_node().tag

    def instantiate_item(self, path: HierarchicalPathName, **attrs):
        """Instantiate the item specified by the path with the given attributes."""
        leaf_node = self[path][-1]
        leaf_node.data.instantiate_item(path.item_name, **attrs)

    def filter_nodes(self, func):
        return self._tree.filter_nodes(func)

    def match_path(self, tokens):
        *heads, leaf_module, item_name = tokens

        candidates = self._tree.all_nodes_itr()

        for h in heads:
            candidates = [c for n in candidates if n.tag == h for c in self._tree.children(n.identifier)]

        for n in candidates:
            if n.tag == leaf_module and n.data.find_decl(item_name):
                yield HierarchicalPathName(n.identifier, item_name)
