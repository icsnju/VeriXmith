from collections import namedtuple
from itertools import pairwise, repeat
from random import getrandbits
from typing import Callable, Iterable

from pysmt.fnode import FNode
from pysmt.logics import QF_UFBV
from pysmt.shortcuts import BV, And, BVZero, BVZExt, EqualsOrIff, FreshSymbol, Iff, Not, Or, Solver
from pysmt.solvers.solver import Model
from pysmt.typing import PySMTType
from tabulate import tabulate

from core.circuits.circuit import Circuit
from core.consts import SMT_SOLVER_TIMEOUT
from core.ir.crossbar import KleeSmtCrossbar, YosysSmtCrossbar, bool_to_bv
from core.ir.module import ItemNotFoundError
from core.ir.view import HierarchicalPathName, ModelTreeView
from core.workspace import get_workspace


def to_readable(smt2_expr: FNode) -> str:
    return smt2_expr.serialize()


CircuitWithState = namedtuple('CircuitWithState', ['circuit', 'state'])


class SmtCircuit(Circuit):
    """An SMT circuit encodes the design with a function.
    1. data: f(state, next_state) -> Boolean
    2. model: structured information of data
    3. state_type: used to define new states
    4. precondition: used to normalize the state space
    5. is_partial: f' => f"""

    def __init__(self, data: Callable[[FNode, FNode], FNode], model: ModelTreeView, state_type: PySMTType,
                 precondition: Callable[[FNode, FNode], FNode], is_partial: bool) -> None:
        super().__init__(data, model)
        self._state_type = state_type
        self.precondition = precondition
        self.is_partial = is_partial

    def new_state(self):
        """Creates an SMT-LIBv2 variable of type state."""
        return FreshSymbol(self._state_type)

    def valid_transformation(self, state: FNode, next_state: FNode):
        """If there is a valid transformation from state to next state."""
        return self.data(state, next_state)

    def signal_value_at_state(self, path: HierarchicalPathName, state: FNode):
        raise NotImplementedError  # implemented by subclasses

    def is_equivalent_to(self, *others, **kwargs) -> bool:

        circuits = [self, *others]
        # Define 2 state variables for each circuit
        current_states = [circuit.new_state() for circuit in circuits]
        next_states = [circuit.new_state() for circuit in circuits]

        # Create an SMT solver
        solver = Solver(name='z3', logic=QF_UFBV, solver_options={'timeout': SMT_SOLVER_TIMEOUT * 1000})

        transformation_exprs = [
            circuit.valid_transformation(current_state, next_state)
            for circuit, current_state, next_state in zip(circuits, current_states, next_states)
        ]

        partial_indices = [idx for idx, circ in enumerate(circuits) if circ.is_partial]

        if not partial_indices:  # Full equivalence
            solver.add_assertion(Not(And(*(Iff(a, b) for a, b in pairwise(transformation_exprs)))))
        elif len(partial_indices) >= 2:
            raise NotImplementedError('comparing >=2 partial models is not supported')
        else:
            partial_expr = transformation_exprs.pop(partial_indices[0])
            solver.add_assertion(And(Or(*(Not(t) for t in transformation_exprs)), partial_expr))
            transformation_exprs.insert(partial_indices[0], partial_expr)

        current_comparator = PairwiseComparator(*(CircuitWithState(circuit, current_state)
                                                  for circuit, current_state in zip(circuits, current_states)))
        next_comparator = PairwiseComparator(*(CircuitWithState(circuit, next_state)
                                               for circuit, next_state in zip(circuits, next_states)))
        self_comparators = (BinaryComparator(CircuitWithState(circuit, current_state),
                                             CircuitWithState(circuit, next_state))
                            for circuit, current_state, next_state in zip(circuits, current_states, next_states))

        equations = list()
        if kwargs['quick']:
            get_equation = lambda ctor, key: ctor.equal_to_specific_value(key)
        else:
            get_equation = lambda ctor, key: ctor.always_equal(key)

        for comparator in (current_comparator, next_comparator):
            # Compare every REGISTER value of current & next state
            equations.extend(get_equation(comparator, 'internal_registers'))

            # Compare every internal NET (wire) value of current & next state
            # NOTE: This is added to avoid bugs caused by certain corner cases in Yosys
            # where an internal wire is defined as an uninterpreted function.
            equations.extend(get_equation(comparator, 'internal_wires'))

        for comparator in (next_comparator, ):
            # Compare every OUTPUT value of next state
            equations.extend(get_equation(comparator, 'output_ports'))

        for comparator in (*self_comparators, current_comparator):
            # Compare every INPUT value of current & next state, this & other model
            # Input ports of THIS circuit hold for 2 cycles
            # Input ports of OTHER circuit hold for 2 cycles
            equations.extend(get_equation(comparator, 'input_ports'))

        # These assertions must be true when inputs and outputs are identical
        solver.add_assertion(And(*equations))
        for circuit, current_state, next_state in zip(circuits, current_states, next_states):
            solver.add_assertion(circuit.precondition(current_state, next_state))

        sat = solver.solve()

        if sat and kwargs['counterexample']:  # Create a failure report

            for key in ('input_ports', 'internal_registers', 'internal_wires'):
                for equation in current_comparator.equal_to_specific_value(key, 0):
                    solver.push()
                    solver.add_assertion(equation)
                    if not solver.solve():
                        solver.pop()
                        assert solver.solve()

            smt_model = solver.get_model()

            report_file = get_workspace().save_to_file('This file was generated after a non-equivalence case found.\n',
                                                       'report.md')
            with open(report_file, 'a') as fp:
                fp.write('\n# Transformation validity:\n')
                validity_vector = tuple(smt_model.get_py_value(expr) for expr in transformation_exprs)
                validity_table = [validity_vector]
                fp.write(
                    tabulate(tabular_data=validity_table,
                             headers=[f'({idx}) {circuit.__class__.__name__}' for idx, circuit in enumerate(circuits)],
                             tablefmt='github'))

                for item_type, state_type in [('internal_registers', 'current'), ('input_ports', 'current/next'),
                                              ('internal_registers', 'next'), ('output_ports', 'next')]:
                    fp.write(f'\n# `{item_type}` of **{state_type}** state:\n')
                    comparator = next_comparator if (state_type == 'next') else current_comparator
                    fp.write(
                        tabulate(tabular_data=comparator.extract_values(item_type, smt_model),
                                 headers=['Signal', 'Value(0)', 'Value(1)'],
                                 tablefmt='github'))

        return not sat


class VariableComparator:

    def always_equal(self, key: ModelTreeView.VIEWS) -> Iterable[FNode]:
        """Assume the signals from the set represented by `key` hold the same value in all circuits at any time."""
        raise NotImplementedError

    def equal_to_specific_value(self, key: ModelTreeView.VIEWS, value: int | None = None) -> Iterable[FNode]:
        """Assume the signals from the set represented by `key` hold the same specific value in all circuits.
        The value is chosen at random if not provided."""
        raise NotImplementedError

    def extract_values(self, key: ModelTreeView.VIEWS, smt_model: Model):
        """Given an SMT model, extract corresponding values of the signals from the set represented by `key`."""
        raise NotImplementedError


class BinaryComparator(VariableComparator):

    def __init__(self, this_circuit_and_state: CircuitWithState, that_circuit_and_state: CircuitWithState) -> None:
        self.this_circuit = this_circuit_and_state.circuit
        self.this_state = this_circuit_and_state.state

        self.that_circuit = that_circuit_and_state.circuit
        self.that_state = that_circuit_and_state.state

    @staticmethod
    def align_width(foo: FNode, bar: FNode) -> tuple[FNode, FNode]:
        """Using zero-extend to make sure given bit-vectors (or booleans) `foo` and `bar` share the same width."""

        foo, bar = map(bool_to_bv, (foo, bar))

        diff = foo.bv_width() - bar.bv_width()
        if diff == 0:
            return foo, bar
        elif diff < 0:  # l shorter than r
            return BVZExt(foo, -diff), bar
        else:  # r shorter than l
            return foo, BVZExt(bar, diff)

    @staticmethod
    def concretize(foo: FNode, value: int | None = None) -> FNode:
        """Sample a value at random. Pin given Boolean/bit-vector to that value."""
        foo = bool_to_bv(foo)
        if value:
            assert 0 <= value < 2**foo.bv_width()
        else:
            value = getrandbits(foo.bv_width())
        return BV(value, foo.bv_width())

    def _align_variables(self, paths: Iterable[HierarchicalPathName]):
        """Find a pair of declarations by `paths` from `this_circuit` and `that_circuit`.
        Depending on the type of instance (primitive / compound), there could be one or more variable pairs to compare.
        Return the item names and the corresponding value pairs that need to be equal."""

        for path in paths:  # NOTE: process paths one by one
            these_values = self.this_circuit.signal_value_at_state(path, self.this_state)
            those_values = self.that_circuit.signal_value_at_state(path, self.that_state)

            this_with_len, that_with_len = (hasattr(x, '__len__') for x in (these_values, those_values))

            if this_with_len and that_with_len:
                assert len(these_values) == len(those_values)

            # At least one of them must support len()
            elif not (this_with_len or that_with_len):
                continue  # Ignore this path

            yield (path.item_name, these_values, those_values)

    def _get_common_paths(self, key: ModelTreeView.VIEWS) -> Iterable[HierarchicalPathName]:
        """Return the intersection set of `key` items in two circuits."""

        if self.this_circuit is self.that_circuit:
            paths = (p for p, _ in getattr(self.this_circuit.model, key))
        else:
            paths_in_this_model = [p for p, _ in getattr(self.this_circuit.model, key)]
            paths_in_other_model = [p for p, _ in getattr(self.that_circuit.model, key)]
            paths = set([*paths_in_this_model, *paths_in_other_model])
        return paths

    def always_equal(self, key: ModelTreeView.VIEWS):
        for _, these_values, those_values in self._align_variables(self._get_common_paths(key)):
            for l, r in zip(these_values, those_values):
                yield EqualsOrIff(*BinaryComparator.align_width(l, r))

    def equal_to_specific_value(self, key: ModelTreeView.VIEWS, value: int | None = None) -> Iterable[FNode]:
        for equation in self.always_equal(key):
            l, r = equation.args()
            if l.is_constant() or r.is_constant():
                yield equation
            else:
                yield And(EqualsOrIff(BinaryComparator.concretize(l, value), r), equation)

    def extract_values(self, key: ModelTreeView.VIEWS, smt_model: Model):

        def get_values(xs) -> str:
            if not hasattr(xs, '__len__'):  # Optimized out
                return 'OPT_OUT'
            else:
                return ','.join(hex(smt_model.get_py_value(x)) for x in xs)

        for name, these_values, those_values in self._align_variables(self._get_common_paths(key)):
            yield (name, get_values(these_values), get_values(those_values))


class PairwiseComparator(VariableComparator):

    def __init__(self, *circuits_and_states: CircuitWithState) -> None:
        if len(circuits_and_states) <= 1:
            raise ValueError('expects 2 or more comparees')
        self.sub_comparators = [BinaryComparator(a, b) for a, b in pairwise(circuits_and_states)]

    def always_equal(self, key: ModelTreeView.VIEWS):
        for sub_comparator in self.sub_comparators:
            yield from sub_comparator.always_equal(key)

    def equal_to_specific_value(self, key: ModelTreeView.VIEWS, value: int | None = None) -> Iterable[FNode]:
        for sub_comparator in self.sub_comparators:
            yield from sub_comparator.equal_to_specific_value(key, value)

    def extract_values(self, key: ModelTreeView.VIEWS, smt_model: Model):
        yield from self.sub_comparators[0].extract_values(key, smt_model)


class YosysSmtCircuit(SmtCircuit):

    def __init__(self, data: Callable[[FNode, FNode], FNode], model: ModelTreeView, state_type: PySMTType,
                 precondition: Callable[[FNode, FNode],
                                        FNode], function_definitions: dict[str, Callable[[FNode], FNode]]) -> None:
        super().__init__(data, model, state_type, precondition, is_partial=False)
        self.function_definitions = function_definitions

    def signal_value_at_state(self, path: HierarchicalPathName, state: FNode):
        cb = YosysSmtCrossbar.from_model(path)
        try:
            return list(f(state) for f in cb.to_data(self.model, self.function_definitions))
        except ItemNotFoundError as err:
            # NOTE: Known cases include: (1) unused wires; (2) array elements never referenced
            if item_decl := err.args[0]:
                if not item_decl.is_register():
                    return repeat(BVZero(1))
            raise


class KleeSmtCircuit(SmtCircuit):

    def signal_value_at_state(self, path: HierarchicalPathName, state: FNode):
        cb = KleeSmtCrossbar.from_model(path)
        try:
            return list(f(state) for f in cb.to_data(self.model))
        except ItemNotFoundError as err:
            # NOTE: only clock signal and wires (in YosysSmtCircuit) are allowed here
            if item_decl := err.args[0]:
                if not item_decl.is_register():
                    return repeat(BVZero(1))
            raise
