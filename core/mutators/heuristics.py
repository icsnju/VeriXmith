import base64
import traceback
from collections import defaultdict
from dataclasses import dataclass
from functools import reduce
from itertools import chain, combinations, pairwise, product, zip_longest
from math import ceil, log10
from operator import add, mul
from pathlib import Path
from random import choice, choices, randint, random, sample, shuffle
from string import ascii_letters
from typing import Iterable

from tree_sitter import Node, Tree

from core.consts import (ALL_DECLARED_IDENTIFIERS, ALL_ESCAPED_IDENTIFIERS, ALL_EXPRESSIONS, ALL_IDENTIFIERS_IN_EXPR,
                         ALL_IDENTIFIERS_WITHOUT_SELECT, ALL_MODULE_DECLARATIONS, ALL_MODULE_INSTANTIATIONS,
                         ALL_NON_ARRAY_ITEM_DECLARATIONS, ALL_REFERENCES, ALL_STATEMENT_OR_NULL, BINARY_EXPRESSIONS,
                         BINARY_NUMBER, BINARY_OPERATORS, CA_NO_SELECT_IN_LHS, COND_STATEMENT_1, COND_STATEMENT_2,
                         DECIMAL_NUMBER, HEX_NUMBER, MODULE_OR_GENERATE_ITEMS, NBA_NO_SELECT_IN_LHS,
                         NONBLOCKING_ASSIGNMENTS, OCTAL_NUMBER, PRIORITY_COEFFICIENT, RANDOM_SELECTION_RATE, RANGE,
                         RHS_EXPRESSIONS, UNARY_EXPRESSIONS, UNARY_OPERATORS, UNSIGNED_NUMBER, VERILOG_COND_TEMPLATE,
                         VERILOG_FUNC_DECL_TEMPLATE, VERILOG_GENERATE_TEMPLATE, VERILOG_LOOP_TEMPLATE, VL_LANGUAGE,
                         parser)
from core.mutators.mutator import MutationError, MutationOperator
from core.workspace import get_workspace


@dataclass
class Replacement:
    """Replace data[start:end] with the substitute."""
    start_byte: int
    end_byte: int
    substitute: bytes


class ByteCoverage:

    def __init__(self, start_byte: int, end_byte: int) -> None:
        self.covered_bytes = [False for _ in range(start_byte, end_byte)]

    def query(self, replacements: Iterable[Replacement]) -> float:
        """Return new_cov * current_cov."""
        to_be_covered = sum(
            (self.covered_bytes[r.start_byte:r.end_byte].count(False) + len(r.substitute) - (r.end_byte - r.start_byte))
            for r in replacements)
        return to_be_covered * self.covered_bytes.count(True)

    def update(self, replacements: Iterable[Replacement]) -> float:
        """Update the coverage array. Return the percentage of covered bytes."""

        # Calculate the remainders of boolean array indicating mutated bytes
        start_bytes = chain((0, ), (r.end_byte for r in replacements))
        end_bytes = chain((r.start_byte for r in replacements), (len(self.covered_bytes), ))
        remainders = (self.covered_bytes[i:j] for i, j in zip(start_bytes, end_bytes))

        # Insert "True" in between the "remainders"
        insertions = ([True] * (r.end_byte - r.start_byte) for r in replacements)

        self.covered_bytes = reduce(lambda x, y: x + y,
                                    chain.from_iterable(zip_longest(remainders, insertions, fillvalue=[])))
        return self.covered_bytes.count(True) / len(self.covered_bytes)


class BytesEditor:
    """This is a helper class for editing the low-level bytes when using tree-sitter.
    Users should not add overlapped intervals between two replacements."""

    @staticmethod
    def calculate_point(data: bytes, offset: int):
        """Calculate the point by offset."""

        line = data.count(b'\n', 0, offset)
        column = offset - (data.rfind(b'\n', 0, offset) + 1)
        return (line, column)

    def __init__(self, data: bytes, replacements: Iterable[Replacement]) -> None:
        self.data = data
        self.replacements = sorted(replacements, key=lambda r: (r.start_byte, r.end_byte))
        for r, s in pairwise(self.replacements):
            assert r.end_byte <= s.start_byte
        self.start_byte = self.replacements[0].start_byte
        self.end_byte = self.replacements[-1].end_byte

    @property
    def start_point(self) -> tuple[int, int]:
        return BytesEditor.calculate_point(self.data, self.start_byte)

    @property
    def end_point(self) -> tuple[int, int]:
        return BytesEditor.calculate_point(self.data, self.end_byte)

    def apply(self) -> None:
        """Apply all scheduled replacement tasks simultaneously."""

        # Calculate the remainders of self.data (old_data)
        start_bytes = chain((0, ), (r.end_byte for r in self.replacements))
        end_bytes = chain((r.start_byte for r in self.replacements), (len(self.data), ))
        remainders = (self.data[i:j] for i, j in zip(start_bytes, end_bytes))

        # Insert "insertions" in between the "remainders"
        insertions = (r.substitute for r in self.replacements)

        new_data = reduce(lambda x, y: x + y, chain.from_iterable(zip_longest(remainders, insertions, fillvalue=b'')))
        new_end_byte = self.end_byte + (len(new_data) - len(self.data))

        # Update data and end_byte
        self.data = new_data
        self.end_byte = new_end_byte
        self.replacements.clear()


@dataclass
class CandidateMutant:
    source: type
    ast: Tree
    replacements: tuple[Replacement, ...]
    cov: ByteCoverage

    @property
    def score(self) -> float:
        """m.priority * k + l.range * mutated_range"""
        return self.source.priority * PRIORITY_COEFFICIENT + self.cov.query(self.replacements)

    def realize(self) -> Tree:
        editor = BytesEditor(self.ast.text, self.replacements)
        editor.apply()
        self.cov.update(self.replacements)
        return parser.parse(editor.data)


def pattern_match(pattern: str,
                  root_node: Node,
                  start_byte: int | None = None,
                  end_byte: int | None = None) -> Iterable[dict[str, Node | list[Node]]]:

    return (m for _, m in VL_LANGUAGE.query(pattern).matches(
        node=root_node, start_byte=(start_byte or root_node.start_byte), end_byte=(end_byte or root_node.end_byte))
            if m)


def _parent_of(n: Node, t: Tree) -> Node:
    """Find the module/task/function declaration where node `n` is declared."""

    cursor = t.root_node.walk()
    if cursor.node.type == 'source_file':
        cursor.goto_first_child()
    while True:
        if cursor.node.type == 'module_declaration' or cursor.node.type == 'package_or_generate_item_declaration':
            if cursor.node.start_byte <= n.start_byte and n.end_byte <= cursor.node.end_byte:
                break
        if not cursor.goto_next_sibling():
            raise MutationError(
                f'cannot find the module where \n{n.text.decode()}\n is declared in \n{t.text.decode()}\n')
    return cursor.node


def _decl_insert_location(parent_node: Node) -> int:
    """Find a location in the parent node to insert new declarations."""

    cursor = parent_node.walk()

    if parent_node.type == 'module_declaration':
        assert cursor.goto_first_child()  # NOTE: "module_or_generate_item" is a child of "module_declaration"
        while True:
            if cursor.node.type == 'module_or_generate_item':
                break
            if not cursor.goto_next_sibling():
                raise MutationError(f'module_or_generate_item not found in \n{parent_node.text.decode()}\n')
        return cursor.node.start_byte
    if parent_node.type == 'package_or_generate_item_declaration':
        assert cursor.goto_first_child()  # task_declaration | function_declaration

        if cursor.node.type == 'task_declaration' or cursor.node.type == 'function_declaration':
            cursor.goto_first_child()  # "task" | "function"
            while cursor.goto_next_sibling():
                continue
            # Reach the last child of "task_declaration | function_declaration"
            cursor.goto_first_child()
            while cursor.goto_next_sibling():
                if cursor.node.type == 'tf_item_declaration':
                    break
            return cursor.node.start_byte

    raise MutationError(f'failed to find an insertion point in \n{parent_node.text.decode()}\n')


def _type_of(id_as_bytes: bytes, module_node: Node) -> str:
    """Find the type of given identifier."""

    assert module_node.type == 'module_declaration'
    declarations = [
        node for node, label in VL_LANGUAGE.query(ALL_DECLARED_IDENTIFIERS.format(
            identifier=id_as_bytes.decode())).captures(module_node) if label == 'declaration'
    ]
    if len(declarations) != 1:
        msg = f'multiple (or zero) declarations of \n{id_as_bytes.decode()}\n found:\n' + '\n'.join(
            f'({i}) {d.text.decode()}'
            for i, d in enumerate(declarations, start=1)) + f'in \n{module_node.text.decode()}\n'
        raise MutationError(msg)

    # Find the "data_type_or_implicit1" node -> get the dimension
    data_type = None

    cursor = declarations[0].walk()
    if cursor.node.type == "list_of_port_declarations":
        assert cursor.goto_first_child()  # '('
        while True:
            if cursor.node.type == 'ansi_port_declaration':
                cursor.goto_first_child()
                if cursor.node.type == 'net_port_header1':
                    cursor.goto_first_child()  # port_direction
                    if cursor.goto_next_sibling():  # net_port_type1
                        assert cursor.goto_first_child()  # data_type_or_implicit1
                        data_type_node = cursor.node
                        assert data_type_node and data_type_node.type == 'data_type_or_implicit1'
                        data_type = data_type_node.text.decode()  # Vector
                        assert cursor.goto_parent()
                    else:
                        data_type = ''  # Scalar
                    cursor.goto_parent()  # Return to "net_port_header1"
                    assert cursor.goto_next_sibling()  # Go to "port_identifier"
                if cursor.node.type == 'port_identifier':
                    if cursor.node.text == id_as_bytes:
                        break
                # Return to "ansi_port_declaration"
                assert cursor.goto_parent()
            if not cursor.goto_next_sibling():
                raise MutationError(f'type of \n{id_as_bytes.decode()}\n not found in \n{module_node.text.decode()}\n')
    elif cursor.node.type == "output_declaration" or cursor.node.type == "input_declaration":
        assert cursor.goto_first_child()
        assert cursor.goto_next_sibling()  # net_port_type1 | list_of_port_identifiers
        assert cursor.goto_first_child()  # net_type | port_identifier
        if cursor.goto_next_sibling():  # data_type_or_implicit1
            assert cursor.node.type == 'data_type_or_implicit1'
            data_type = cursor.node.text.decode()
        else:  # port_identifier
            data_type = ''
    elif cursor.node.type == 'parameter_declaration':
        assert cursor.goto_first_child()
        assert cursor.goto_next_sibling()
        if cursor.node.type == 'implicit_data_type1':
            data_type = cursor.node.text.decode()
        else:
            data_type = ''
    elif cursor.node.type == 'tf_item_declaration':
        assert cursor.goto_first_child()  # tf_port_declaration
        assert cursor.node.type == 'tf_port_declaration'
        assert cursor.goto_first_child()  # tf_port_direction
        while cursor.goto_next_sibling():
            if cursor.node.type == 'data_type_or_implicit1':
                data_type = cursor.node.text.decode()
                break
        else:  # no "data_type_or_implicit1" child found
            data_type = ''
    elif cursor.node.type == 'net_declaration':
        assert cursor.goto_first_child()  # net_type
        assert cursor.goto_next_sibling()  # data_type_or_implicit1 | list_of_net_decl_assignments
        if cursor.node.type == 'data_type_or_implicit1':
            data_type = cursor.node.text.decode()
        else:
            data_type = ''
    else:  # "data_declaration"
        assert cursor.goto_first_child()  # data_type_or_implicit1
        assert cursor.goto_first_child()  # data_type
        if cursor.node.child_count == 1:
            data_type = ''
        else:  # NOTE: "signed" variable has 3 children
            data_type = ' '.join(child.text.decode() for child in cursor.node.children[1:])

    return data_type


def _range_of(id_as_bytes: bytes, module_node: Node) -> tuple[str, str]:
    """Get the (msb, lsb) pair of given identifier."""
    if r := RANGE.findall(_type_of(id_as_bytes, module_node)):
        if len(r) == 1:
            msb, lsb = r[0]
            return (msb, lsb)
    raise NotImplementedError('identifier with multiple ranges and scalar are not supported')


def _shape_of(id_as_bytes: bytes, module_node: Node) -> tuple[int, int]:

    def parse_number(s: str) -> int:
        while s.startswith('(') and s.endswith(')'):
            s = s[1:-1]

        if m := UNSIGNED_NUMBER.fullmatch(s):
            return int(m.group('decimal').replace('_', ''))
        elif m := DECIMAL_NUMBER.fullmatch(s):
            return int(m.group('decimal').replace('_', ''))
        elif m := BINARY_NUMBER.fullmatch(s):
            return int(m.group('binary').replace('_', ''), base=2)
        elif m := OCTAL_NUMBER.fullmatch(s):
            return int(m.group('octal').replace('_', ''), base=8)
        elif m := HEX_NUMBER.fullmatch(s):
            return int(m.group('hex').replace('_', ''), base=16)
        else:
            raise NotImplementedError(f'invalid number format: {s}')

    msb, lsb = _range_of(id_as_bytes, module_node)
    return (parse_number(msb), parse_number(lsb))


def _random_id(length: int = 5) -> str:
    """Randomly generate an identifier for a variable created during mutation."""
    return '_' + ''.join(choices(ascii_letters, k=length))


class BaseMutator:

    priority = 0
    percentage = 1.0

    def __init__(self, tree: Tree) -> None:
        self.tree = tree

    def mutate_plans(self) -> Iterable[tuple[Replacement, ...]]:
        """A base mutator searches for its target pattern in content[start_byte:end_byte].
        Return all matched patterns. Exits silently if no target pattern was found."""
        raise NotImplementedError

    def candidates(self, cov: ByteCoverage) -> Iterable[CandidateMutant]:
        for rs in self.mutate_plans():
            yield CandidateMutant(self.__class__, self.tree, rs, cov)


class ChangeUnaryOp(BaseMutator):

    def mutate_plans(self) -> Iterable[tuple[Replacement, ...]]:
        for uop_match in pattern_match(UNARY_EXPRESSIONS, self.tree.root_node):
            uop_node = uop_match['uop']
            assert isinstance(uop_node, Node)
            expr_node = uop_match['expr']
            assert isinstance(expr_node, Node)
            yield (
                # Replace the old unary operator with a randomly chosen new unary operator
                Replacement(uop_node.start_byte, uop_node.end_byte,
                            choice(UNARY_OPERATORS).encode()),
                # Add a pair of parentheses
                Replacement(expr_node.start_byte, expr_node.start_byte, b'('),
                Replacement(expr_node.end_byte, expr_node.end_byte, b')'))


class ChangeBinaryOp(BaseMutator):

    def mutate_plans(self) -> Iterable[tuple[Replacement, ...]]:
        for bop_match in pattern_match(BINARY_EXPRESSIONS, self.tree.root_node):
            bop_node = bop_match['bop']
            assert isinstance(bop_node, Node)
            # Replace the old binary operator with a randomly chosen new binary operator
            yield (Replacement(bop_node.start_byte, bop_node.end_byte, choice(BINARY_OPERATORS).encode()), )


class DuplicateExpr(BaseMutator):

    def make_inserted(self, operand: str) -> bytes:
        return f'({operand} {choice(BINARY_OPERATORS)} {operand})'.encode()

    def mutate_plans(self) -> Iterable[tuple[Replacement, ...]]:
        for expr_match in pattern_match(RHS_EXPRESSIONS, self.tree.root_node):
            outer_expr = expr_match['expr']
            assert isinstance(outer_expr, Node)
            # Randomly choose an expression inside the outer expr
            if sub_exprs := [
                    n for m in pattern_match(ALL_EXPRESSIONS, outer_expr) if isinstance((n := m['expr']), Node)
            ]:
                expr_node = choice(sub_exprs)
                yield (Replacement(expr_node.start_byte, expr_node.end_byte,
                                   self.make_inserted(expr_node.text.decode())), )


class MakeRepeat(BaseMutator):
    """Wrap an existing statement with `repeat`."""

    def make_parameter(self, param_name: str) -> bytes:
        """Generate the parameter declaration"""
        return f'parameter {param_name} = 1;\n'.encode()

    def make_prefix(self, param_name: str) -> bytes:
        return f'repeat ({param_name}) '.encode()

    def mutate_plans(self) -> Iterable[tuple[Replacement, ...]]:
        # Randomly choose a "statement_or_null" node
        for stmt_match in pattern_match(ALL_STATEMENT_OR_NULL, self.tree.root_node):
            stmt_node = stmt_match['stmt']
            assert isinstance(stmt_node, Node)
            decl_location = _decl_insert_location(_parent_of(stmt_node, self.tree))
            param_name = _random_id(5)
            yield (
                Replacement(decl_location, decl_location, self.make_parameter(param_name)),  # Insert the declaration
                Replacement(stmt_node.start_byte, stmt_node.start_byte,
                            self.make_prefix(param_name)),  # Replace the stmt with the loop statement
            )


class MakeLoopGenerate(BaseMutator):
    """Wrap existing statements with loop generate constructs."""

    def mutate_plans(self) -> Iterable[tuple[Replacement, ...]]:
        # Randomly choose a "module_or_generate_item" node
        for item_match in pattern_match(MODULE_OR_GENERATE_ITEMS, self.tree.root_node):
            item_node = item_match['item']
            assert isinstance(item_node, Node)

            decl_location = _decl_insert_location(_parent_of(item_node, self.tree))
            genvar_name = _random_id(3)
            genvar_decl = f'genvar {genvar_name};\n'.encode()

            yield (
                # Insert the genvar declaration
                Replacement(decl_location, decl_location, genvar_decl),
                # Replace the "module_or_generate_item" with the generate block
                Replacement(
                    item_node.start_byte, item_node.end_byte,
                    VERILOG_GENERATE_TEMPLATE.format(genvar=genvar_name,
                                                     module_or_generate_item=item_node.text.decode()).encode()),
            )


class DuplicateCond1(BaseMutator):

    def combine_two_cond(self, cond_a: Node, cond_b: Node) -> bytes:
        return f'{cond_a.text.decode()} {choice(BINARY_OPERATORS)} {cond_b.text.decode()}'.encode()

    def mutate_plans(self) -> Iterable[tuple[Replacement, ...]]:
        for module_match in pattern_match(ALL_MODULE_DECLARATIONS, self.tree.root_node):
            module_node = module_match['module']
            assert isinstance(module_node, Node)

            for cond_match_a, cond_match_b in combinations(
                    pattern_match(COND_STATEMENT_1 + COND_STATEMENT_2, module_node), 2):
                cond_node_a, cond_node_b = cond_match_a['cond'], cond_match_b['cond']
                assert isinstance(cond_node_a, Node)
                assert isinstance(cond_node_b, Node)
                new_cond = self.combine_two_cond(cond_node_a, cond_node_b)
                yield (Replacement(cond_node_a.start_byte, cond_node_a.end_byte,
                                   new_cond), Replacement(cond_node_b.start_byte, cond_node_b.end_byte, new_cond))


class DuplicateCond2(BaseMutator):

    def extract_nba(self, cond_node: Node, stmt_node: Node, if_location: int) -> Iterable[tuple[Replacement, ...]]:
        for nba_match in pattern_match(NONBLOCKING_ASSIGNMENTS, stmt_node):
            nba_node = nba_match['nba']
            assert isinstance(nba_node, Node)

            yield (Replacement(nba_node.start_byte, nba_node.end_byte, b''),
                   Replacement(
                       if_location, if_location,
                       VERILOG_COND_TEMPLATE.format(cond_expr=cond_node.text.decode(),
                                                    statement=nba_node.text.decode()).encode()))

    def mutate_plans(self) -> Iterable[tuple[Replacement, ...]]:
        for module_match in pattern_match(ALL_MODULE_DECLARATIONS, self.tree.root_node):
            module_node = module_match['module']
            assert isinstance(module_node, Node)

            for cond_match in pattern_match(COND_STATEMENT_1, module_node):
                stmt_node = cond_match['stmt']
                assert isinstance(stmt_node, Node)
                cond_node = cond_match['cond']
                assert isinstance(cond_node, Node)

                yield from self.extract_nba(cond_node, stmt_node, stmt_node.end_byte)

            for cond_match in pattern_match(COND_STATEMENT_2, module_node):
                stmt_nodes = cond_match['stmt']
                assert isinstance(stmt_nodes, list) and len(stmt_nodes) == 2
                cond_node = cond_match['cond']
                assert isinstance(cond_node, Node)

                # NOTE: For a typical conditional statement. Both the "then" and "else" blocks may be matched as "stmt".
                # Therefore, the new statement may be inserted before the else, which breaks the original correspondence
                # of if-else.
                # if (cond) <then block> else <else block>
                for stmt_node in stmt_nodes:
                    yield from self.extract_nba(cond_node, stmt_node, stmt_node.end_byte)


class RemoveCond(BaseMutator):

    def mutate_plans(self) -> Iterable[tuple[Replacement, ...]]:
        for cond_match in pattern_match(COND_STATEMENT_1, self.tree.root_node):
            stmt_node = cond_match['stmt']
            assert isinstance(stmt_node, Node)
            if_node = cond_match['if']
            assert isinstance(if_node, Node)

            yield (Replacement(if_node.start_byte, if_node.end_byte, stmt_node.text), )

        for cond_match in pattern_match(COND_STATEMENT_2, self.tree.root_node):
            stmt_nodes = cond_match['stmt']
            assert isinstance(stmt_nodes, list) and len(stmt_nodes) == 2
            if_node = cond_match['if']
            assert isinstance(if_node, Node)

            yield (Replacement(if_node.start_byte, if_node.end_byte, stmt_nodes[0].text + b'\n' + stmt_nodes[1].text), )


class SplitAssignment(BaseMutator):

    def make_bit_assignments(self, assign_node: Node, lvalue_node: Node, rvalue_node: Node, msb: int, lsb: int,
                             is_nba: bool) -> bytes:

        offsets = [(x - assign_node.start_byte)
                   for x in (lvalue_node.end_byte, rvalue_node.start_byte, rvalue_node.end_byte)]
        remainders = [assign_node.text[i:j] for i, j in pairwise((0, *offsets, len(assign_node.text)))]
        assert len(remainders) == 4

        def insert_indices(index: bytes) -> bytes:
            return remainders[0] + b'[' + index + b']' + remainders[1] + b'(' + remainders[
                2] + b') >> ' + index + remainders[3]

        assignments = b'\n'.join(insert_indices(str(i).encode()) for i in range(min(msb, lsb), max(msb, lsb) + 1))
        if is_nba:
            assignments = b'\nbegin\n' + assignments + b'\nend\n'
        return assignments

    def mutate_plans(self) -> Iterable[tuple[Replacement, ...]]:
        for assign_match in pattern_match(CA_NO_SELECT_IN_LHS + NBA_NO_SELECT_IN_LHS, self.tree.root_node):
            rvalue_node = assign_match['rvalue']
            assert isinstance(rvalue_node, Node)

            # NOTE: Split "concatenation" expression is likely to produce constant assignments
            cursor = rvalue_node.walk()
            if cursor.goto_first_child():  # "primary"
                if cursor.goto_first_child():  # skip "concatenation"
                    if cursor.node.type == 'concatenation':
                        continue

            assign_node = assign_match['assignment']
            assert isinstance(assign_node, Node)
            lvalue_node = assign_match['lvalue']
            assert isinstance(lvalue_node, Node)

            try:  # If the range of lvalue is constant
                msb, lsb = _shape_of(lvalue_node.text, _parent_of(lvalue_node, self.tree))
            except NotImplementedError:
                continue

            yield (Replacement(
                assign_node.start_byte, assign_node.end_byte,
                self.make_bit_assignments(assign_node, lvalue_node, rvalue_node, msb, lsb,
                                          assign_node.type == 'statement_item')), )


class LoopAssignment(BaseMutator):

    def make_loop_body(self, lvalue_node: Node, rvalue_node: Node, index: bytes) -> Iterable[Replacement]:
        suffix = b'[' + index + b']'
        yield from (Replacement(lvalue_node.end_byte, lvalue_node.end_byte,
                                suffix), Replacement(rvalue_node.start_byte, rvalue_node.start_byte, b'('),
                    Replacement(rvalue_node.end_byte, rvalue_node.end_byte, b') >> ' + index))

    def make_loop_header(self, template: str, genvar: str, start: str, end: str) -> str:
        return template.format(genvar=genvar, start=start, end=end)

    def mutate_plans(self) -> Iterable[tuple[Replacement, ...]]:
        for assign_match in pattern_match(CA_NO_SELECT_IN_LHS, self.tree.root_node):
            rvalue_node = assign_match['rvalue']
            assert isinstance(rvalue_node, Node)

            # NOTE: Split "concatenation" expression is likely to produce constant assignments
            cursor = rvalue_node.walk()
            if cursor.goto_first_child():  # "primary"
                if cursor.goto_first_child():  # skip "concatenation"
                    if cursor.node.type == 'concatenation':
                        continue

            assign_node = assign_match['assignment']
            assert isinstance(assign_node, Node)
            lvalue_node = assign_match['lvalue']
            assert isinstance(lvalue_node, Node)

            # Decide loop start and end by the range of lvalue
            # NOTE: We assume that msb >= lsb
            try:
                end, start = _range_of(lvalue_node.text, _parent_of(lvalue_node, self.tree))
            except NotImplementedError:
                continue

            genvar_name = _random_id()
            genvar_decl = f'genvar {genvar_name};\n'.encode()
            insert_location = _decl_insert_location(_parent_of(assign_node, self.tree))

            for_loop = self.make_loop_header(VERILOG_LOOP_TEMPLATE, genvar_name, start, end)

            yield (
                # The genvar declaration
                Replacement(insert_location, insert_location, genvar_decl),
                # The generated for-loop
                Replacement(assign_node.start_byte, assign_node.start_byte, for_loop.encode()),
                # Insert indices after identifiers
                *self.make_loop_body(lvalue_node, rvalue_node, genvar_name.encode()))


class RedundantAssignment(BaseMutator):

    def make_bit_assignment(self, assign_node: Node, index: bytes) -> Iterable[Replacement]:
        identifiers = (n for m in pattern_match(ALL_IDENTIFIERS_WITHOUT_SELECT, assign_node)
                       if isinstance((n := m['identifier']), Node))
        suffix = b'[' + index + b']'
        yield from (Replacement(id_node.end_byte, id_node.end_byte, suffix) for id_node in identifiers)

    def mutate_plans(self) -> Iterable[tuple[Replacement, ...]]:
        # For a continuous or non-blocking assignment
        for assign_match in pattern_match(CA_NO_SELECT_IN_LHS + NBA_NO_SELECT_IN_LHS, self.tree.root_node):
            assign_node = assign_match['assignment']
            assert isinstance(assign_node, Node)
            lvalue_node = assign_match['lvalue']
            assert isinstance(lvalue_node, Node)

            try:
                # Decide index value
                index = choice(_range_of(lvalue_node.text, _parent_of(lvalue_node, self.tree)))
            except NotImplementedError:
                continue

            prefix = b'\nbegin\n' if assign_node.type == 'statement_item' else b''
            suffix = b'\nend\n' if assign_node.type == 'statement_item' else b''

            yield (
                # Duplicate the original assignment
                *self.make_bit_assignment(assign_node, index.encode()),
                # Insert indices after identifiers
                Replacement(assign_node.start_byte, assign_node.start_byte, prefix + assign_node.text),
                Replacement(assign_node.end_byte, assign_node.end_byte, suffix))


class MakeArray(BaseMutator):

    def update_ref(self, module_node: Node, identifier_node: Node, shape: list[int]) -> Iterable[Replacement]:
        # Find all its references in the same module
        for ref_match in pattern_match(ALL_REFERENCES.format(identifier=identifier_node.text.decode()), module_node):
            if 'id-lhs' in ref_match:
                ref_node = ref_match['id-lhs']
                updated_ref = self.complete_ref(identifier_node.text, shape)
            elif 'id-in-expr' in ref_match:
                ref_node = ref_match['id-in-expr']
                updated_ref = self.partial_ref(identifier_node.text, shape)
            else:
                continue
            assert isinstance(ref_node, Node)
            yield Replacement(ref_node.start_byte, ref_node.end_byte, updated_ref)

    def partial_ref(self, identifier: bytes, shape: list[int]) -> bytes:
        all_references = [(identifier.decode() + '[' + ']['.join(map(str, indices)) + ']')
                          for indices in product(*(range(s) for s in shape))]
        return ('{' + ",".join(choices(all_references, k=randint(1, reduce(mul, shape)))) + '}').encode()

    def complete_ref(self, identifier: bytes, shape: list[int]) -> bytes:
        all_references = [(identifier.decode() + '[' + ']['.join(map(str, indices)) + ']')
                          for indices in product(*(range(s + 1) for s in shape))]
        shuffle(all_references)
        return ('{' + ",".join(all_references) + '}').encode()

    def make_declaration_suffix(self, shape: list[int]) -> bytes:
        # Decide the range of this dimension : [start:end]
        return ''.join(f'[{start}:{end}]' for start, end in (choice([(0, size), (size, 0)]) for size in shape)).encode()

    def mutate_plans(self) -> Iterable[tuple[Replacement, ...]]:
        # Randomly choose an identifier and its decl_assignment block from the content
        for item_decl_match in pattern_match(ALL_NON_ARRAY_ITEM_DECLARATIONS, self.tree.root_node):
            identifier_node = item_decl_match['identifier']
            assert isinstance(identifier_node, Node)
            decl_assign_node = item_decl_match['decl_assignment']
            assert isinstance(decl_assign_node, Node)

            module_node = _parent_of(identifier_node, self.tree)
            if module_node.type != 'module_declaration':
                continue

            # Decide the shape of the generated array
            shape = [randint(2, 5) for _ in range(randint(1, 2))]

            yield (
                # Replace the declaration: id => id[...]
                Replacement(identifier_node.end_byte, decl_assign_node.end_byte, self.make_declaration_suffix(shape)),
                # Replace the references: id => id[x]
                *self.update_ref(module_node, identifier_node, shape))


class MakeFunction(BaseMutator):
    """Turn an expression into a function definition & insert subroutine calls."""

    def replaceable_exprs(self, module_node: Node) -> list[Node]:
        """Find all expressions (may be overlapped) in this module."""
        search_space = chain(
            (n for m in pattern_match(RHS_EXPRESSIONS, module_node) if isinstance((n := m['expr']), Node)),
            (n for m in pattern_match(COND_STATEMENT_1 + COND_STATEMENT_2, module_node)
             if isinstance((n := m['if']), Node)))
        return [
            n for s in search_space for m in pattern_match(ALL_EXPRESSIONS, s) if isinstance((n := m['expr']), Node)
        ]

    def choose_arguments(self, parameter_count: int, replaceable_exprs: list[Node]) -> str:
        return ', '.join(f'({arg.text.decode()})' for arg in choices(replaceable_exprs, k=parameter_count))

    def make_func_call(self, function_name: str, arguments: str) -> str:
        return f'{function_name}({arguments})'

    def to_be_replaced(self, replaceable_exprs: list[Node], number: int) -> list[Node]:
        # Replace some of these expressions with the generated function call
        exprs_to_be_replaced = sample(replaceable_exprs, k=number)
        min_start_byte = 0
        # NOTE: These expressions must be non-overlapped
        for node in sorted(exprs_to_be_replaced, key=lambda n: n.start_byte):
            if node.start_byte >= min_start_byte:
                min_start_byte = node.end_byte
            else:
                exprs_to_be_replaced.remove(node)
        return exprs_to_be_replaced

    def make_replacements(self, function_name: str, parameter_count: int,
                          replaceable_exprs: list[Node]) -> Iterable[Replacement]:
        max_replacements = ceil(log10(len(replaceable_exprs))) + 1  # NOTE: restrict the number of function call
        for expr in self.to_be_replaced(replaceable_exprs, randint(1, max_replacements)):
            yield Replacement(
                expr.start_byte, expr.end_byte,
                self.make_func_call(function_name, self.choose_arguments(parameter_count, replaceable_exprs)).encode())

    def make_func_decl(self, module_node: Node, function_name: str) -> tuple[str, int] | None:
        # For all existing right-hand-side expressions in the same module
        all_rhs_exprs = list(pattern_match(RHS_EXPRESSIONS, module_node))
        if all_rhs_exprs:
            # Select one as the function body
            func_body = choice(all_rhs_exprs)['expr']
            assert isinstance(func_body, Node)

            # Find all identifiers in the expression
            # NOTE: Use "set" to deduplicate these identifiers
            id_refs = set(n.text for m in pattern_match(ALL_IDENTIFIERS_IN_EXPR, func_body)
                          if isinstance((n := m['identifier']), Node))

            parameter_count = len(id_refs)
            # Construct the input declaration list
            input_declarations = '\n'.join(f'input {_type_of(i, module_node)} {i.decode()};' for i in id_refs)

            func_decl = VERILOG_FUNC_DECL_TEMPLATE.format(function_name=function_name,
                                                          input_declarations=input_declarations,
                                                          expression=func_body.text.decode())

            return func_decl, parameter_count

    def mutate_plans(self) -> Iterable[tuple[Replacement, ...]]:
        # Choose a module
        for module_match in pattern_match(ALL_MODULE_DECLARATIONS, self.tree.root_node):
            module_node = module_match['module']
            assert isinstance(module_node, Node)
            # Find the point to insert the function declaration
            func_location = _decl_insert_location(module_node)

            function_name = _random_id()
            replaceable_exprs = self.replaceable_exprs(module_node)
            if replaceable_exprs and (pair := self.make_func_decl(module_node, function_name)):
                func_decl, parameter_count = pair
                yield (
                    # Insert the function declaration
                    Replacement(func_location, func_location, func_decl.encode()),
                    # Replace selected expressions with the function call
                    *self.make_replacements(function_name, parameter_count, replaceable_exprs))


class DuplicateModule(BaseMutator):

    def replace_instantiations(self, old_name: bytes, new_name: bytes) -> Iterable[Replacement]:
        """Check if the given module is instantiated at least twice. Randomly sample a subset of instantiations.
        Replace the module identifier in chosen subset with newly generated name. Returns corresponding replacements.
        Otherwise, returns None."""

        instantiations = list(
            pattern_match(ALL_MODULE_INSTANTIATIONS.format(identifier=old_name.decode()), self.tree.root_node))
        if (inst_cnt := len(instantiations)) >= 2:
            for inst_match in sample(instantiations, k=randint(1, inst_cnt - 1)):
                name_node = inst_match['module_name']
                assert isinstance(name_node, Node)
                yield Replacement(name_node.start_byte, name_node.end_byte, new_name)
        else:
            raise NotImplementedError

    def make_module_name(self, old_name: bytes) -> bytes:
        return old_name + _random_id(3).encode()

    def mutate_plans(self) -> Iterable[tuple[Replacement, ...]]:
        # For each module in the program
        for module_match in pattern_match(ALL_MODULE_DECLARATIONS, self.tree.root_node):
            module_node = module_match['module']
            assert isinstance(module_node, Node)
            module_name = module_match['module_name']
            assert isinstance(module_name, Node)

            duplicated_module_name = self.make_module_name(module_name.text)

            try:
                yield (
                    # Rename the module in current declaration
                    Replacement(module_name.start_byte, module_name.end_byte, duplicated_module_name),
                    # Replace the module identifier in chosen subset with newly generated name
                    *self.replace_instantiations(module_name.text, duplicated_module_name),
                    # Append the original module declaration after the program
                    Replacement(self.tree.root_node.end_byte, self.tree.root_node.end_byte, b'\n' + module_node.text))
            except NotImplementedError:
                continue


def encode_escaped_identifiers(tree: Tree, content: bytes, start_byte: int, end_byte: int) -> tuple[bytes, int, int]:
    """Replace all escaped identifiers with simple identifiers."""

    def simplify(escaped_id: bytes) -> bytes:
        return b'___' + base64.b64encode(escaped_id, altchars=b'$_').replace(b'=', b'')

    escaped_ids = VL_LANGUAGE.query(ALL_ESCAPED_IDENTIFIERS).captures(tree.root_node,
                                                                      start_byte=start_byte,
                                                                      end_byte=end_byte)
    editor = BytesEditor(content, (Replacement(id_node.start_byte, id_node.end_byte, simplify(id_node.text))
                                   for id_node, _ in escaped_ids))

    old_end_point = editor.end_point
    editor.apply()

    # Update the tree
    tree.edit(start_byte, end_byte, editor.end_byte, editor.start_point, old_end_point, editor.end_point)
    return editor.data, start_byte, editor.end_byte


class HeuristicMutator(MutationOperator):
    """Perform semantic-aware mutations."""

    def __init__(self, *configuration) -> None:
        super().__init__()
        self.sub_mutators = [sub_mutator for sub_mutator, *_ in configuration]
        denominator = sum(percentage for _, _, percentage in configuration)
        for sub_mutator, priority, percentage in configuration:
            sub_mutator.priority = priority
            sub_mutator.percentage = percentage / denominator

    @classmethod
    def default(cls):
        return cls(
            (ChangeUnaryOp, 0, 1),
            (ChangeBinaryOp, 0, 1),
            (MakeLoopGenerate, 0, 1),
            (MakeRepeat, 0, 1),
            (RedundantAssignment, 0, 2),
            (RemoveCond, 1, 2),
            (DuplicateModule, 1, 2),
            (DuplicateExpr, 1, 2),
            (DuplicateCond1, 1, 3),
            (DuplicateCond2, 1, 3),
            (MakeFunction, 2, 3),
            (SplitAssignment, 0, 3),
            (MakeArray, 1, 5),
            (LoopAssignment, 2, 5),
        )

    def candidates_of(self, ast: Tree, cov: ByteCoverage) -> dict[type[BaseMutator], list[CandidateMutant]]:
        candidates = defaultdict(list)
        workspace = get_workspace()
        # Collect mutation candidates for each sub-mutator
        for m in self.sub_mutators:
            try:
                candidates[m].extend(m(ast).candidates(cov))
            except MutationError:
                error_src = workspace.save_to_file(ast.text, 'mutation_error')
                workspace.save_to_file(traceback.format_exc(), error_src.with_suffix('.log'))
                self.has_error = True
        return candidates

    def generate(self, seed_path: Path, number: int) -> Iterable[bytes]:
        MAX_NUM = number * 3

        seed = seed_path.read_bytes()
        seed_ast = parser.parse(seed)

        # Initialization: Collect all candidates for current seed
        candidates = self.candidates_of(seed_ast, ByteCoverage(0, len(seed)))

        for _ in range(number):
            mutants_by_priority = sorted(chain(*candidates.values()), key=lambda c: c.score, reverse=True)

            while mutants_by_priority:
                # Selection: (1) Mutator + Location with the highest priority
                #            (2) Randomly choose a pair from the queue
                chosen = 0 if random() < RANDOM_SELECTION_RATE else randint(0, len(mutants_by_priority) - 1)
                next_mutant = mutants_by_priority.pop(chosen)

                # Mutation: Try to realize the candidate mutant.
                mutant_ast = next_mutant.realize()

                if self.__class__.validate(mutant_ast.text, seed_path.suffix):
                    yield mutant_ast.text
                    # NOTE: At this point, the content has just been validated.
                    # Therefore, any "ERROR" node found in the tree indicates problems of the parser.
                    if mutant_ast.root_node.has_error:
                        get_workspace().save_to_file(mutant_ast.text, 'parse_error' + seed_path.suffix)
                        self.has_error = True
                    else:
                        # Propagation: If succeeded, put the mutant's descendants into "candidates".
                        for mutator_type, mutants in self.candidates_of(mutant_ast, next_mutant.cov).items():
                            candidates[mutator_type].extend(mutants)
                        # Drop some mutants randomly if too many of them were generated
                        mutant_cnt = reduce(add, (len(l) for l in candidates.values()))
                        for mutator_type, mutants in candidates.items():
                            if (expected := int(min(MAX_NUM, mutant_cnt) * mutator_type.percentage)) < len(mutants):
                                candidates[mutator_type] = sorted(mutants, key=lambda c: c.score,
                                                                  reverse=True)[:expected]
                    break
