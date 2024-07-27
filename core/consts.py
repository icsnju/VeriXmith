import re

from pysmt.typing import BV8, BV32, ArrayType, Type
from tree_sitter import Language, Parser

# ------------------------------------- Tree-sitter's Verilog parser and queries ------------------------------------- #

VL_LANGUAGE = Language('/tmp/tree-sitter.so', 'verilog')

# Create the Verilog/SystemVerilog parser
parser = Parser()
parser.set_language(VL_LANGUAGE)

REG_DECLARATION = VL_LANGUAGE.query('''
(package_or_generate_item_declaration
    (data_declaration
        (data_type_or_implicit1
            (data_type
                (integer_vector_type "reg")))
        (list_of_variable_decl_assignments
[
            (variable_decl_assignment
                (simple_identifier) @comment .)
            (variable_decl_assignment
                (simple_identifier) @comment
                (expression))
            (variable_decl_assignment
                (simple_identifier)
                (unpacked_dimension)+) @comment
])))
''')

REG_DECLARATIONS_IN_MODULE = VL_LANGUAGE.query('''
(module_declaration
    (module_header
        (simple_identifier) @module-id)
    (module_or_generate_item
        (package_or_generate_item_declaration
            (data_declaration
                (data_type_or_implicit1
                    (data_type
                        (integer_vector_type "reg")))
                (list_of_variable_decl_assignments
                    (variable_decl_assignment
                        (simple_identifier) @reg-id))))))
''')

ALL_DECLARED_IDENTIFIERS = '''
(list_of_port_declarations
    (ansi_port_declaration
        (port_identifier
            (simple_identifier) @identifier (#eq? @identifier "{identifier}")))) @declaration

(output_declaration
    (list_of_port_identifiers
        (port_identifier
            (simple_identifier) @identifier (#eq? @identifier "{identifier}")))) @declaration

(input_declaration
    (list_of_port_identifiers
        (port_identifier
            (simple_identifier) @identifier (#eq? @identifier "{identifier}")))) @declaration

(parameter_declaration
    (list_of_param_assignments
        (param_assignment
            (parameter_identifier
                (simple_identifier) @identifier (#eq? @identifier "{identifier}"))))) @declaration

(tf_item_declaration
    (tf_port_declaration
        (list_of_tf_variable_identifiers
            (port_identifier
                (simple_identifier) @identifier (#eq? @identifier "{identifier}"))))) @declaration

(net_declaration
    (list_of_net_decl_assignments
        (net_decl_assignment
            (simple_identifier) @identifier (#eq? @identifier "{identifier}")))) @declaration

(module_or_generate_item
    (package_or_generate_item_declaration
        (data_declaration
            (list_of_variable_decl_assignments
                (variable_decl_assignment
                    (simple_identifier) @identifier (#eq? @identifier "{identifier}")))) @declaration))
'''

# NOTE: Ports cannot be arrays in Verilog
ALL_NON_ARRAY_ITEM_DECLARATIONS = '''
(module_declaration
    (module_or_generate_item
        (package_or_generate_item_declaration
            [(net_declaration
                (list_of_net_decl_assignments
                    (net_decl_assignment
                        (simple_identifier) @identifier (#not-match? @identifier "(clk|clock)")) @decl_assignment))
            (data_declaration
                (list_of_variable_decl_assignments
                    (variable_decl_assignment
                        (simple_identifier) @identifier (#not-match? @identifier "(clk|clock)")) @decl_assignment))])))
'''

ALL_REFERENCES = '''
(expression
    (primary
        (simple_identifier) @id-in-expr (#eq? @id-in-expr "{identifier}")))
(variable_lvalue
    (simple_identifier) @id-lhs (#eq? @id-lhs "{identifier}"))
(net_lvalue
    (simple_identifier) @id-lhs (#eq? @id-lhs "{identifier}"))
'''

ALL_IDENTIFIERS_IN_EXPR = '''
(expression
    (primary
        (simple_identifier) @identifier))
'''

ALL_IDENTIFIERS_WITHOUT_SELECT = '''
(variable_lvalue
    (simple_identifier) @identifier .)
(net_lvalue
    (simple_identifier) @identifier .)
(expression
    (primary
        (simple_identifier) @identifier .))
'''

ALL_ESCAPED_IDENTIFIERS = '''(escaped_identifier) @identifier'''

ALL_EXPRESSIONS = '''
((expression) @expr
    (#not-match? @expr "(clk|clock)"))
'''

RHS_EXPRESSIONS = '''
(continuous_assign
    (list_of_net_assignments
        (net_assignment
            (expression) @expr)))
(nonblocking_assignment
    (expression) @expr)
'''

CA_NO_SELECT_IN_LHS = '''
(module_or_generate_item
    (continuous_assign
        (list_of_net_assignments .
            (net_assignment
                (net_lvalue
                    (simple_identifier) .) @lvalue
                (expression) @rvalue) .))) @assignment
'''

NBA_NO_SELECT_IN_LHS = '''
(statement_item
    (nonblocking_assignment
        (variable_lvalue
            (simple_identifier) .) @lvalue
        (expression) @rvalue)) @assignment
'''

ALL_STATEMENT_OR_NULL = '''
(statement_or_null) @stmt
(function_statement_or_null) @stmt
'''

ALL_MODULE_DECLARATIONS = '''
(module_declaration
    (module_header
        (simple_identifier) @module_name)) @module
'''

ALL_MODULE_INSTANTIATIONS = '''
(module_instantiation
    (simple_identifier) @module_name (#eq? @module_name "{identifier}"))
'''

NONBLOCKING_ASSIGNMENTS = '''
(statement_item
    (nonblocking_assignment)) @nba
'''

MODULE_OR_GENERATE_ITEMS = '''
(module_or_generate_item
    [
        (continuous_assign)
        (always_construct)
    ]) @item
'''

COND_STATEMENT_1 = '''
(conditional_statement
    (cond_predicate) @cond .
    (statement_or_null) @stmt . ) @if
'''

COND_STATEMENT_2 = '''
(conditional_statement
    (cond_predicate) @cond
    (statement_or_null) @stmt
    "else"
    (statement_or_null) @stmt ) @if
'''

UNARY_EXPRESSIONS = '''
(expression
    . (unary_operator) @uop) @expr
(constant_expression
    . (unary_operator) @uop) @expr
'''

BINARY_EXPRESSIONS = '''
(expression
    [
        "**"
        "*" "/" "%"
        "+" "-"
        "<<" ">>" "<<<" ">>>"
        "<" "<=" ">" ">="
        "==" "!=" "===" "!=="
        "&"
        "^" "^~" "~^"
        "|"
        "&&"
        "||"
    ] @bop)
'''

# --------------------------------------- Constants used by heuristic mutators --------------------------------------- #

PRIORITY_COEFFICIENT = 100

UNARY_OPERATORS = ('+', '-', '!', '~', '&', '~&', '|', '~|', '^', '~^', '^~')
BINARY_OPERATORS = ("**", "*", "/", "%", "+", "-", "<<", ">>", "<<<", ">>>", "<", "<=", ">", ">=", "==", "!=", "===",
                    "!==", "&", "^", "^~", "~^", "|", "&&", "||")

RANGE = re.compile(r'\[(?P<msb>.*):(?P<lsb>.*)\]')
UNSIGNED_NUMBER = re.compile(r'(?P<decimal>\d[_\d]*)')
DECIMAL_NUMBER = re.compile(r"([1-9][_\d]*)?'[sS]?[dD](?P<decimal>\d[_\d]*)")
BINARY_NUMBER = re.compile(r"([1-9][_\d]*)?'[sS]?[bB](?P<binary>[0-1][_0-1]*)")
OCTAL_NUMBER = re.compile(r"([1-9][_\d]*)?'[sS]?[oO](?P<octal>[0-7][_0-7]*)")
HEX_NUMBER = re.compile(r"([1-9][_\d]*)?'[sS]?[hH](?P<hex>[0-9a-fA-F][_0-9a-fA-F]*)")

RANDOM_SELECTION_RATE = 0.5

VERILOG_GENERATE_TEMPLATE = '''
generate
    for ({genvar}=0; {genvar}<1; {genvar}={genvar}+1) begin
        {module_or_generate_item}
    end
endgenerate
'''

VERILOG_LOOP_TEMPLATE = '''
for ({genvar}=({start}); {genvar}<=({end}); {genvar}={genvar}+1)
'''

VERILOG_COND_TEMPLATE = '''
if ({cond_expr}) begin
    {statement}
end
'''

VERILOG_FUNC_DECL_TEMPLATE = '''
function {function_name};
    {input_declarations}
    {function_name} = {expression};
endfunction
'''

# -------------------------------------- C++ code template used in YosysWriteCxx ------------------------------------- #

DEBUG_CPP_TEMPLATE = '''
#include "{top_module}.cpp"
#include <fstream>

const char SEP = ',';

int main()
{{
    cxxrtl_design::p_{top_module} top;
    cxxrtl::debug_items items;
    top.debug_info(&items, nullptr, "");

    std::ofstream f("debug_info.csv");
    if (f.is_open())
    {{
        f << "name,width,next,flags\\n";
        for (auto &it : items.table)
            for (auto &part : it.second)
                f << it.first << SEP << part.width << SEP << part.next << SEP << part.flags << '\\n';
        f.close();
    }}
    else
        return -1;
    return 0;
}}
'''

SYM_EXE_CPP_TEMPLATE = '''
#include "{top_module}.cpp"
#include <klee/klee.h>
#include <string>

typedef struct state
{{
    {struct_definition}
}} state;

state s;

void snapshot(cxxrtl::debug_items &items)
{{
    {snapshot_function_definition}
}}

int main()
{{
    cxxrtl_design::p_{top_module} top;
    cxxrtl::debug_items items;
    top.debug_info(&items, nullptr, "");

    // Set up the symbolic variables
    {initialization}

    top.commit();

    // Save the state before posedge
    {debug_eval}
    snapshot(items);
    klee_save_snapshot(&s);

    {set_posedge}
    top.step();

    // Save the state after posedge
    {debug_eval}
    snapshot(items);
    klee_save_snapshot(&s);

    return 0;
}}
'''

# -------------------------------------- Other constants used in MetaTranslators ------------------------------------- #

KLEE_ARRAY_TYPE = ArrayType(BV32, BV8)
KLEE_ARRAY_DECL = re.compile(r'\(declare-fun \w+ \(\) \(Array \(_ BitVec 32\) \(_ BitVec 8\) \) \)')
KLEE_STATE_TYPE = Type('Klee-State')

SMT2_INITIAL_STATE = re.compile(r'\w+_is')
SMT2_ACCESSOR_NAME = re.compile(r'(?P<mod>\w+)_n (?P<wirename>[\w\[\]\$]+)')
SMT2_OTHER_HIERARCHY = re.compile(r'(?P<mod>\w+)_h (?P<wirename>[\w\[\]\$]+)')

CXXRTL_CLK_FUNC_DEF = re.compile(r'bool posedge_p_(?P<clk_name>[\w\$]+)\(\) const {')
CXXRTL_DEBUG_EVAL_FUNC_DEF = re.compile(r'void debug_eval\(\);')
CXXRTL_DEBUG_EVAL_STMT = 'top.debug_eval();'

SIMPLE_IDENTIFIER = re.compile(r'[a-zA-Z_][a-zA-Z0-9_\$]*')

VERILATOR_VAR_DEF = re.compile(r'^\s*// - "(?P<name>\w+)"\n$')

LL_DEBUG_INFO = re.compile(r'!\d+ = !DIDerivedType\('
                           r'tag: DW_TAG_member, '
                           r'name: "(?P<name>\w+)", '
                           r'scope: !\d+, file: !\d+, '
                           r'line: \d+, baseType: !\d+, '
                           r'size: (?P<size>\d+)'
                           r'(, align: (?P<align>\d+))?'
                           r'(, offset: (?P<offset>\d+))?'
                           r'(, flags: DIFlagPublic)?\)\n')
LL_FILENAME = re.compile(r'!\d+ = !DIFile\('
                         r'filename: "./V(?P<top_module>\w+)___024root.h", '
                         r'directory: "[\w/]+"\)\n')

# ----------------------------------------------- Test harness settings ---------------------------------------------- #

DEFAULT_TIMEOUT = 100
KLEE_TIMEOUT = 1000
SMT_SOLVER_TIMEOUT = 1000

INPUT_FILENAME = 'input'
STRATEGY_FILENAME = 'strategy.json'
EXCEPTION_FILENAME = 'exception.log'
DIFFERENCE_FILENAME = 'equivalence_classes'
