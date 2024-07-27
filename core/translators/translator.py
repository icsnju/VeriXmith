from functools import reduce
from itertools import combinations
from operator import mul
from random import choice

from core.circuits.circuit import Circuit
from core.world import WorldMap


class CmdlineOption:

    def __init__(self, template, domain=(None, )) -> None:
        self.template = template
        self.domain = domain

    def sample(self) -> str:
        return self.template.format(choice(self.domain))

    def count(self) -> int:
        return len(list(self.domain)) + 1


class MetaTranslator:

    edges = None

    alternative_options = list()

    def __init_subclass__(cls) -> None:
        super().__init_subclass__()
        if not cls.edges:
            raise ValueError('a translator must have at least one pair of start-end types')
        WorldMap.draw(cls)

    def __init__(self, policy: dict | None = None) -> None:
        self.policy = policy

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}({self.policy})'

    def translate(self, circuit: Circuit) -> Circuit:
        raise NotImplementedError

    @classmethod
    def all_instances(cls, max_op: int):
        if cls.alternative_options:
            for op_cnt in range(max_op):
                for ops in combinations((op.sample() for op in cls.alternative_options), op_cnt):
                    yield cls({'extra_args': ops})
        else:
            yield cls()

    @classmethod
    def instance_count(cls) -> int:
        return reduce(mul, (op.count() for op in cls.alternative_options), 1)


class Conversion:
    """A path containing >=1 translators."""

    def __init__(self, *translators: MetaTranslator) -> None:
        self._translator_chain = translators

    def __repr__(self) -> str:
        return f'{self.__class__.__name__}({", ".join(repr(t) for t in self._translator_chain)})'

    def apply_to(self, circuit: Circuit) -> Circuit:
        for translator in self._translator_chain:
            circuit = translator.translate(circuit)
        return circuit
