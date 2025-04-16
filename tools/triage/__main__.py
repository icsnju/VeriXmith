import csv
import json
import logging
import os
import subprocess
from collections import defaultdict
from datetime import datetime
from functools import reduce
from itertools import chain, product
from operator import add, and_, or_
from pathlib import Path
from shutil import move, rmtree

import typer
from rich.logging import RichHandler

app = typer.Typer()

logging.basicConfig(level=logging.WARNING, format='%(message)s', handlers=[RichHandler()])


def iterate_subdirs(parent_dir: Path):
    yield from (p for p in parent_dir.iterdir() if p.is_dir())


def inspect_subdir(dirpath: Path, trait: dict[str, list]) -> bool:
    try:
        return reduce(and_, (predicate_maker(expressions)((dirpath / filename).read_text())
                             for filename, expressions in trait.items()), True)
    except FileNotFoundError:
        return False


def predicate_maker(expressions):
    operator, *operands = expressions
    match operator:
        case 'AND':
            operator, initial = and_, True
        case 'OR':
            operator, initial = or_, False
        case 'NOT':
            if (op_cnt := len(operands)) != 1:
                raise ValueError(f'"NOT" takes exactly one argument ({op_cnt} given)')
            operator, initial = lambda _, x: not x, None
        case _:
            raise NotImplementedError

    def predicate(content: str) -> bool:
        return reduce(operator, ((x in content) if isinstance(x, str) else predicate_maker(x)(content)
                                 for x in operands), initial)

    return predicate


def load_filter(filepath: str) -> dict:
    return json.load(open(filepath))


def extract_datetime(dirname: str) -> datetime:
    return datetime.strptime(dirname[:dirname.find('_tmp')], '%Y%m%d_%H%M%S_%f')


def apply_filter(parent_dir: Path, patterns: dict) -> list[datetime]:
    first_occurrences = list()
    for name, spec in patterns.items():
        trait = spec.pop('__trait')
        apply_trait(parent_dir, name, trait)

        child_dir = parent_dir / name

        ignore = spec.pop('__ignore', False)
        if ignore:
            rmtree(child_dir)
        elif not os.listdir(child_dir):
            os.rmdir(child_dir)
        elif spec:
            if sub_categories := apply_filter(child_dir, spec):
                first_occurrences.extend(sub_categories)
            else:
                logging.warning(f'Failed to match any sub-category in {name}.')
                first_occurrences.extend(
                    extract_datetime(c.name) for c in iterate_subdirs(child_dir) if c.name.startswith('2024'))
        else:
            if subdirs := sorted(c.name for c in iterate_subdirs(child_dir) if c.name.startswith('2024')):
                first_occurrences.append(extract_datetime(subdirs[0]))

    if unclassified := [d for d in os.listdir(parent_dir) if d.startswith('2024')]:
        logging.warning(f'{len(unclassified)} unclassified bug reports under {parent_dir.as_posix()}.')

    return first_occurrences


def apply_trait(parent_dir: Path, category: str, trait: dict):
    """Move all subdirs matching given trait under parent_dir to parent_dir/category."""

    target_dir = parent_dir / category
    target_dir.mkdir(parents=True, exist_ok=True)

    for subdir in iterate_subdirs(parent_dir):
        if (not subdir.samefile(target_dir)) and inspect_subdir(subdir, trait):
            move(subdir, target_dir)


TRANSFORMERS = ('SurelogPlugin', 'SystemVerilogToVerilog', 'VerilatorTransformer', 'YosysWriteSmt2', 'YosysWriteCxx')


def get_transformer_and_args(line: str) -> tuple[str | None, tuple[str, ...]]:

    def get_extra_args(dict_str: str) -> tuple[str, ...]:
        return eval(dict_str)['extra_args']

    for t in TRANSFORMERS:
        if (start := line.find(t + '(')) > 0:
            if t == 'YosysWriteSmt2':
                return t, tuple()
            line = line[start + len(t) + 1:]
            if (end := line.find('})')) > 0:
                return t, get_extra_args(line[:end + 1])
    return None, tuple()  # No transformer found


def get_equivalence_classes(dirpath: Path) -> list[dict[str, list[tuple[str, ...]]]]:
    classes = list()
    try:
        with open(dirpath / 'equivalence_classes', 'r') as fp:
            current_class = None
            for line in fp.readlines():
                if 'core.circuits' in line:  # New equivalence class
                    if current_class:
                        classes.append(current_class)
                    current_class = defaultdict(list)
                t, args = get_transformer_and_args(line)
                if t:
                    current_class[t].append(args)
            if current_class:
                classes.append(current_class)

    except FileNotFoundError:
        if dirpath.name.startswith('2024'):
            logging.warning(f'cannot find the equivalence classes in {dirpath.as_posix()}')
    return classes


def diff_classifier(parent_dir: Path) -> int:
    category_count = set()
    for subdir in iterate_subdirs(parent_dir):
        if classes := get_equivalence_classes(subdir):
            prime_category = sub_category = None

            # Prime: use the correlated transformers
            prime_category = '-'.join(sorted(chain(*(c.keys() for c in classes))))

            # Sub: minority transformer type
            if len(classes) == 2 and len(classes[0]) == 1 and len(classes[1]) == 1:
                sub_category = prime_category
            else:
                minority = sorted(classes, key=lambda c: reduce(add, (len(i) for i in c.values())))[0]
                if len(minority) > 1:
                    sub_category = '-'.join(sorted(minority.keys()))
                else:
                    t, args = list(minority.items())[0]
                    sub_category = t + ''.join(chain(*args))

            if prime_category and sub_category:
                category_count.add((prime_category, sub_category))
                dst = parent_dir / prime_category / sub_category
                try:
                    dst.mkdir(parents=True, exist_ok=False)
                except FileExistsError:
                    pass
                move(subdir, dst)

        elif subdir.name.startswith('2024'):
            logging.warning(f'failed to load classes from {subdir.as_posix()}')

    return len(category_count)


def result_dirs_in(parent_dir: Path, tasks: dict) -> list[tuple[Path, str, int, int, bool]]:
    failures = list()
    for name in tasks['InputSets']:
        for m, v, c in product(tasks['Configurations']['Mutants'], tasks['Configurations']['Validations'],
                               tasks['Configurations']['Completeness']):
            result_dir = list(parent_dir.glob(f'{name}-{m}-{v}-{c}-*-failures/'))
            assert len(result_dir) == 1
            failures.append((result_dir[0], name, m, v, c))
    return sorted(failures)


@app.command()
def sort(parent_dir: str, config_file: str):
    """Sort the parent_dir with patterns in config_file."""
    apply_filter(Path(parent_dir), load_filter(config_file))


@app.command()
def count(tasks_file: str, parent_dir: str, output_csv: str):

    parent_path = Path(parent_dir)
    # Unzip *-failures.tar.gz files
    for ar in parent_path.glob('*-failures.tar.gz'):
        target_dir = ar.with_name(ar.name.strip('.tar.gz'))
        if target_dir.exists() and target_dir.is_dir():
            rmtree(target_dir)
        target_dir.mkdir()
        subprocess.run(['tar', 'xf', ar.as_posix(), f'--directory={target_dir.as_posix()}', '--warning=no-timestamp'])

    data = list()
    data.append([
        'InputSets', 'Mutants', 'Validations', 'Completeness', 'Compilation Space Failures',
        'Cross-checking Space Failures'
    ])

    for result_dir, name, m, v, c in result_dirs_in(parent_path, json.load(open(tasks_file))):

        unique_compilation_failures = len(
            apply_filter(result_dir / 'compilation', load_filter('tools/triage/crash_filter.json')))
        logging.info(f'Detected {unique_compilation_failures} unique crash(es) in {result_dir.as_posix()}.')

        unique_cross_checking_failures = len(
            apply_filter(result_dir / 'cross-checking', load_filter('tools/triage/crash_filter.json')))
        unique_cross_checking_failures += diff_classifier(result_dir / 'cross-checking')
        logging.info(f'Detected {unique_cross_checking_failures} unique difference(s) in {result_dir.as_posix()}.')

        # Modify the "InputSet" string
        language, source = name.split('-')
        start, end = Path(output_csv).stem.split('-')
        name = f'{language}[{source}]({start},{end})'

        data.append([name, m, v, c, unique_compilation_failures, unique_cross_checking_failures])

    writer = csv.writer(open(output_csv, 'w'))
    writer.writerows(data)


if __name__ == '__main__':
    app()
