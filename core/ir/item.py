import re
from enum import Enum


class PortDirection(Enum):
    """Direction of the port in a module declaration."""

    INPUT = 1
    OUTPUT = 2
    INOUT = 3

    @staticmethod
    def parse(direction):
        """Convert the given string into its corresponding enumeration."""

        if isinstance(direction, PortDirection) or direction is None:
            return direction
        elif isinstance(direction, str):
            if direction == 'input':
                return PortDirection.INPUT
            elif direction == 'output':
                return PortDirection.OUTPUT
            elif direction == 'inout':
                return PortDirection.INOUT
        else:
            raise ValueError(f'Unknown type of direction: {direction}')


class ModuleItem:
    """This abstract base class represents the data type in a circuit (typically in Verilog)."""

    def is_register(self) -> bool:
        raise NotImplementedError

    def is_port(self) -> bool:
        raise NotImplementedError

    def is_instance(self):
        raise NotImplementedError

    def new_instance(self, **kwargs):
        raise NotImplementedError


class PrimitiveItem(ModuleItem):
    """Similar to primitive data types in software programming world."""

    def is_register(self) -> bool:
        return self._is_reg

    def is_wide(self) -> bool:
        return self.width > 64

    def is_port(self) -> bool:
        return self._direction is not None

    def is_input_port(self) -> bool:
        return self._direction == PortDirection.INPUT

    def is_output_port(self) -> bool:
        return self._direction == PortDirection.OUTPUT

    def is_instance(self) -> bool:
        return self._inst_attrs is not None

    @property
    def width(self) -> int:
        return self._width

    @property
    def name(self) -> str:
        return self._name

    def __init__(self,
                 name: str,
                 width: int,
                 is_reg: bool,
                 direction: str | PortDirection | None = None,
                 inst_attrs: dict | None = None) -> None:
        self._name = name
        self._width = width
        self._is_reg = is_reg
        self._direction = PortDirection.parse(direction)
        self._inst_attrs = inst_attrs

    def __getattr__(self, name):
        inst_attrs = super().__getattribute__('_inst_attrs')
        if inst_attrs is not None and name in inst_attrs:
            return inst_attrs[name]
        else:
            raise AttributeError(f'{name} is not set in this item')

    def __repr__(self) -> str:
        attrs = (f'{k}={v}' for k, v in self.__dict__.items())

        descriptions = list()
        if self.is_wide():
            descriptions.append('Wide')
        if self.is_port():
            descriptions.append('Port')
        if self.is_instance():
            descriptions.append('Instance')
        else:
            descriptions.append('Declaration')

        return f'{self.__class__.__name__}[{",".join(descriptions)}]({",".join(attrs)})'

    def new_instance(self, **kwargs):
        """This method creates a new `PrimitiveItem` instance with given new attributes attached."""
        return self.__class__(self.name, self.width, self._is_reg, self._direction, kwargs)


class CompoundItem(ModuleItem):

    # NOTE: Currently, the internal representation of Yosys has a serious defect.
    # For example, consider following declarations:
    #   (1) wire a [1:0];
    #   (2) wire \a[1] , \a[0] ;
    # In Yosys, both (1) and (2) will be represented as "a[1]" and "a[0]", which means that
    # we are unable to tell the difference between array elements and others with escaped identifiers.
    # NOTE: To solve this conflict, we treat all these identifiers as (1).
    ARRAY_ELEMENT = re.compile(r'(?P<name>[!-~]+)\[(?P<index>\d+)\]')

    @staticmethod
    def register_element(compound_items_in_module: dict[str, ModuleItem], module_name: str, item_name: str,
                         is_reg: bool, element_index: int, element_width: int):
        """Normally, a compound item contains several primitive items as its elements.
        The elements are added one by one. Each element has its unique index. Sometimes their names are different too.
        When a new element is added, following things should be done:
        1. Find the target item in the given module.
        2. Check if the new element fits in the item.
        3. Increase capacity.

        `compound_items_in_module` is a Dict object holds all compound items in current module.
        If the CompoundItem object is not created, create it first then return the item.

        This method is invoked when constructing a Verilog circuit."""

        item = compound_items_in_module.setdefault(item_name, CompoundItem(item_name, is_reg, element_width))
        assert isinstance(item, CompoundItem)

        # NOTE: Every element should have the same datatype and width. Besides, each element must have an unique index.
        # This may help locate mysterious failures early in the process.
        if is_reg != item.is_register():
            raise ValueError(f'incompatible types of elements in {module_name}.{item_name}')
        if element_width != item._element_width:
            raise ValueError(f'incompatible widths of elements in {module_name}.{item_name}')
        if element_index in item._element_indices:
            raise ValueError(f'duplicate index {element_index} (already have {item._element_indices})')

        item._element_indices.add(element_index)
        return item

    def __init__(self,
                 name: str,
                 is_reg: bool,
                 element_width: int,
                 element_indices: set[int] | None = None,
                 inst_attrs: dict | None = None) -> None:
        self._name = name
        self._is_reg = is_reg
        self._element_width = element_width
        self._element_indices = element_indices or set()
        self._inst_attrs = inst_attrs

    @property
    def name(self) -> str:
        return self._name

    @property
    def capacity(self) -> int:
        return len(self._element_indices)

    def __getattr__(self, name):
        inst_attrs = super().__getattribute__('_inst_attrs')
        if inst_attrs is not None and name in inst_attrs:
            return inst_attrs[name]
        else:
            raise AttributeError(f'{name} is not set in this item')

    def __repr__(self) -> str:
        attrs = (f'{k}={v}' for k, v in self.__dict__.items())

        descriptions = list()
        if self.is_instance():
            descriptions.append('Instance')
        else:
            descriptions.append('Declaration')

        return f'{self.__class__.__name__}[{",".join(descriptions)}]({",".join(attrs)})'

    def is_instance(self):
        return self._inst_attrs is not None

    def is_port(self):
        return False

    def is_register(self) -> bool:
        return self._is_reg

    def new_instance(self, **kwargs):
        """Two situations may occur when instantiating a compound item:
        1. Instantiated once: instantiate all its elements.
        2. Instance each element individually: first instantiate all; verify consistency later.

        NOTE: this method returns a new `CompoundItem` object."""

        if self.is_instance():
            # verify consistency
            assert self._inst_attrs == kwargs
            return self
        else:
            return self.__class__(self._name, self._is_reg, self._element_width, self._element_indices, kwargs)
