import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime
from itertools import product
from os import environ, remove
from pathlib import Path
from shlex import quote
from shutil import copyfile, copytree, rmtree
from typing import Literal, Optional

import typer
from rich.logging import RichHandler
from rich.progress import Progress
from typing_extensions import Annotated

app = typer.Typer()

PREFIX = environ['HOME']

logging.basicConfig(level=logging.INFO, format='%(message)s', handlers=[RichHandler()])


def log_subprocess_output(shell_cmd, check=False, **kwargs):
    """Start a subprocess and redirect its stdout & stderr to logging."""
    process = subprocess.Popen(shell_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
    for line in process.stdout:
        logging.info(line.decode())
    process.stdout.close()
    return_code = process.wait()
    if check and return_code:
        raise subprocess.CalledProcessError(return_code, shell_cmd)


def timestamped_name(prefix: str, suffix: str) -> str:
    """Insert a timestamp between prefix and suffix."""
    return '-'.join((prefix, datetime.today().strftime('%Y%m%d_%H%M%S_%f'), suffix))


def upload_to_remote(source: str, hostname: str, destination: str):
    """Copy the source file to the remote server."""
    log_subprocess_output(['rsync', source, f'{hostname}:{destination}'], check=True)


def pack_and_upload(dirpath: str, hostname: str, destination: str, archive_name: str = 'data.tar.gz') -> str:
    """Create an archive from dirpath; then copy to remote destination.
    Return the destination path on the remote host."""

    archive_path = Path(archive_name).absolute()
    input_dir_path = Path(dirpath).absolute()
    log_subprocess_output(
        ['tar', 'czf', archive_path.as_posix(), f'--directory={input_dir_path.as_posix()}', '.'],
        cwd=input_dir_path.parent,
        check=True)
    logging.info(f'Packaged [{input_dir_path.as_posix()}] into [{archive_path.as_posix()}].')

    upload_to_remote(archive_path.as_posix(), hostname, destination)
    logging.info(f'Moved [{archive_path.as_posix()}] to [{hostname}:{destination}].')
    remove(archive_path.as_posix())

    return (Path(destination) / archive_name).absolute().as_posix()


def download_from_remote(hostname: str, source: str, destination: str):
    """Download the source file from the remote server."""
    log_subprocess_output(['rsync', f'{hostname}:{source}', destination], check=True)


def download_and_cleanup(hostname: str, source: str, destination: str):
    """Download the source file from the remote server; then remove it from the server."""
    try:
        download_from_remote(hostname, source, destination)
    except subprocess.CalledProcessError:
        logging.warning(f'Failed to copy [{source}] from [{hostname}].')
    else:
        log_subprocess_output(f'ssh {hostname} rm {source}', shell=True)


def extract_input_and_strategy(parent_dir: str, input_filename: str, strategy_filename: str) -> str:
    """Extract the input & strategy files to a subdirectory under `parent_dir`.
    Return path to the directory holding extracted files."""

    regression_input_dir = Path(parent_dir) / 'regression_input'
    if regression_input_dir.exists():
        rmtree(regression_input_dir)
    regression_input_dir.mkdir()

    # Collect the (input, strategy) pairs
    logging.info(f'Collecting the (input, strategy) pairs to [{regression_input_dir.as_posix()}]...')
    index = 1
    for case_dir in (d for d in Path(parent_dir).iterdir() if d.is_dir()):
        input_file = case_dir / input_filename
        strategy_file = case_dir / strategy_filename
        if input_file.exists() and strategy_file.exists():
            copyfile(input_file, regression_input_dir / (f'input{index:0>6}' + input_file.suffix))
            copyfile(strategy_file, regression_input_dir / (f'strategy{index:0>6}' + strategy_file.suffix))
            index += 1

    return regression_input_dir.absolute().as_posix()


def start_task(
        task_name: Literal['default', 'regression', 'mutate'],
        input_archive_or_directory: str,
        output_archive_or_directory: str,
        n_jobs: int,
        n_cpus: int,
        /,
        *,
        # Shared options
        seed: str = '',
        max_time: str = '',
        hostname: str | None = None,
        # Options for "default"
        n_validations: int = 0,
        source_node: str = 'unused',
        sink_node: str = 'unused',
        quick: bool = False,
        # Options for "regression"
        input_suffix: str = '.v',
        # Options for "mutate"
        n_times: int = 0):
    """Run `start_task.sh` with given arguments."""

    options = f'seed={seed} \
        max_time={max_time} \
        n_validations={n_validations} \
        source_node={source_node} sink_node={sink_node} \
        quick={quick} \
        input_suffix={input_suffix} \
        n_times={n_times}'

    prefix = f'ssh {hostname} {options} bash -s -- <' if hostname else f'{options} bash'

    log_subprocess_output(' '.join([
        prefix, 'tools/deploy/start_task.sh', task_name,
        quote(input_archive_or_directory),
        quote(output_archive_or_directory),
        str(n_jobs),
        str(n_cpus)
    ]),
                          shell=True,
                          check=True)


@dataclass
class Configuration:
    mutants: int
    validations: int
    completeness: bool

    @staticmethod
    def from_dict(d: dict):
        return [Configuration(m, v, c) for m, v, c in product(d['Mutants'], d['Validations'], d['Completeness'])]

    def runner_maker(self, input_set_name: str, dirpath: str, size: int, timeout: str = '0'):

        def runner(result_path, n_jobs, n_cpus, hostname):
            if input_set_name.startswith('Verilog'):
                source_node = 'VerilogCircuit'
                sink_node = 'SmtCircuit'
                input_suffix = '.v'
            elif input_set_name.startswith('SystemVerilog'):
                source_node = 'SystemVerilogCircuit'
                sink_node = 'VerilogCircuit'
                input_suffix = '.sv'
            else:
                raise NotImplementedError

            identifier = f'{input_set_name}-{self.mutants}-{self.validations}-{self.completeness}'

            # NOTE: Expected structure of this result path:
            # result-path/
            #   [input-set]-[n]x[n]/
            #   *-results/ (or *-results.tar.gz)
            #   *-mutants/ (or *-mutants.tar.gz)

            seed_dir = result_path / f'{input_set_name}-{size}x1'
            input_dir = result_path / f'{input_set_name}-{size}x{self.mutants + 1}'

            # NOTE: Avoid mutating or sampling for many times
            if not seed_dir.exists():
                test_case_slicer(f'{dirpath}/*{input_suffix}', 1, size, seed_dir.as_posix(), shuffle=True)
            if not input_dir.exists():
                # Copy seeds from seed_dir
                copytree(seed_dir, input_dir)

                mutate(identifier,
                       seed_dir.as_posix(),
                       result_path.as_posix(),
                       self.mutants,
                       n_jobs,
                       n_cpus,
                       hostname=hostname)

                if hostname:
                    merge_mutants = f'ls {result_path.as_posix()}/"{identifier}"-*-mutants.tar.gz | \
                    sort -r | head -n 1 | \
                    xargs -I _ tar -xmf _ --directory {input_dir} --wildcards "*{input_suffix}" --warning no-timestamp'

                else:
                    merge_mutants = f'mv {result_path.as_posix()}/"{identifier}"-*-mutants/*{input_suffix} \
                        {input_dir.as_posix()}'

                log_subprocess_output(merge_mutants, shell=True, check=True)

            if not list(result_path.glob(identifier + '-*-results' + '.tar.gz' if hostname else '')):
                batch_test(identifier,
                           input_dir.as_posix(),
                           result_path.as_posix(),
                           self.validations,
                           source_node,
                           sink_node,
                           n_jobs,
                           n_cpus,
                           max_time=timeout,
                           quick=not self.completeness,
                           hostname=hostname)

        return runner


@app.command()
def verismith_generate(
        size: Annotated[int, typer.Argument(help='Number of Verilog files to be generated')],
        output_dir: Annotated[str, typer.Argument(help='Path to the directory for output files')],
        output_ar: Annotated[Optional[str], typer.Option(help='Name of the output archive file')] = None,
        config_file: Annotated[str, typer.Option(
            help='Path to the Verismith configuration file')] = 'dependencies/verismith/verismith_config.toml',
        script_file: Annotated[str, typer.Option(
            help='Path to the script invoking Verismith')] = 'dependencies/verismith/verismith_task.sh',
        max_loc: Annotated[int, typer.Option(help='Max lines of generated program')] = 200):
    """Generate a package of the Verilog data."""

    config_path = Path(config_file)
    script_path = Path(script_file)

    result_path = Path(output_dir)
    result_path.mkdir(parents=True)

    log_subprocess_output(f'docker run -ti --rm \
        --mount type=bind,source={config_path.absolute().as_posix()},target=/app/verismith/{config_path.name} \
        --mount type=bind,source={script_path.absolute().as_posix()},target=/app/verismith/{script_path.name} \
        --mount type=bind,source={result_path.absolute().as_posix()},target=/app/verismith/{result_path.name} \
        --entrypoint bash \
        verismith verismith_task.sh {size} {config_path.name} {result_path.name} {max_loc}',
                          shell=True,
                          check=True)

    # Change the owner of files generated in Docker container
    log_subprocess_output(['sudo', 'chown', '-R', 'zyk:zyk', result_path.as_posix()])

    if output_ar:
        log_subprocess_output(['tar', 'czf', output_ar, f'--directory={result_path.as_posix()}', '.'])
        rmtree(result_path.as_posix())


@app.command()
def vlog_hammer_generate(output_dir: str, project_dir: str = 'dependencies/vlog-hammer'):
    """Generate Verilog input with Vlog-Hammer."""

    log_subprocess_output(['make', 'purge', 'generate'], cwd=project_dir, check=True)
    log_subprocess_output(['mv', f'{project_dir}/rtl', output_dir], check=True)


@app.command()
def test_case_slicer(pattern: str, start: int, end: int, output_dir: str, shuffle: bool = False):
    """Extract a subset of files matching `pattern` to `output_dir`."""

    output_path = Path(output_dir)
    output_path.mkdir(parents=True)
    log_subprocess_output(f'ls -1 {pattern}{" | shuf" if shuffle else ""} \
            | head -n {end} | tail -n {end - start + 1} \
            | xargs -I _ cp _ {quote(output_path.as_posix())}',
                          shell=True,
                          check=True)


def deploy_helper(input_dir: str, output_dir: str, output_identifier: str, hostname: str | None) -> tuple[str, str]:

    if hostname:  # Deploy on given remote server (only one)
        # Copy the input content to remote server at $PREFIX
        input_path = pack_and_upload(input_dir, hostname, PREFIX)
        # When running on remote servers, pack output files into archives
        output_path = f'{PREFIX}/{output_identifier}.tar.gz'

    else:  # Run on local machine
        # Input path is not changed
        input_path = input_dir
        # Output directory is created if not exist
        output_path = Path(output_dir) / output_identifier
        output_path.mkdir(parents=True, exist_ok=True)
        output_path = output_path.absolute().as_posix()

    return input_path, output_path


@app.command()
def batch_test(label: Annotated[str, typer.Argument(help='Label for this group of tests')],
               input_path: Annotated[str, typer.Argument(help='Path to the directory containing input files')],
               result_path: Annotated[str, typer.Argument(help='Path to the output directory')],
               n_validations: Annotated[int, typer.Argument(help='Number of validations applied to each input')],
               source_node: Annotated[str, typer.Argument(help='Name of the source node in tv-graph')],
               sink_node: Annotated[str, typer.Argument(help='Name of the sink node in tv-graph')],
               n_jobs: Annotated[int, typer.Argument(help='Number of workers')],
               n_cpus: Annotated[int, typer.Argument(help='Number of CPUs')],
               max_time: Annotated[str, typer.Option(help='Path to the statistics file')] = '',
               quick: Annotated[bool, typer.Option(help='Path to the statistics file')] = False,
               seed: Annotated[str, typer.Option(help='Random seed')] = '',
               hostname: Annotated[Optional[str], typer.Option(help='Name of the remote server')] = None):
    """Start a new test campaign."""

    input_path, output_path = deploy_helper(input_path, result_path, timestamped_name(label, 'results'), hostname)

    logging.info(f'Starting the job...')
    start_task('default',
               input_path,
               output_path,
               n_jobs,
               n_cpus,
               n_validations=n_validations,
               source_node=source_node,
               sink_node=sink_node,
               max_time=max_time,
               quick=quick,
               seed=seed,
               hostname=hostname)

    if hostname:
        download_and_cleanup(hostname, output_path, result_path)

    logging.info('Test done!')


@app.command()
def regression_test(
        label: Annotated[str, typer.Argument(help='Label for this group of tests')],
        parent_dir: Annotated[str, typer.Argument(help='Each of its subdir contains a pair of (input, strategy)')],
        result_path: Annotated[str, typer.Argument(help='Path to the output directory')],
        n_jobs: Annotated[int, typer.Argument(help='Number of workers')],
        n_cpus: Annotated[int, typer.Argument(help='Number of CPUs')],
        input_filename: Annotated[str, typer.Option(help='Name of the input file')] = 'input.v',
        strategy_filename: Annotated[str, typer.Option(help='Name of the strategy file')] = 'strategy.json',
        hostname: Annotated[Optional[str], typer.Option(help='Name of the remote server')] = None):
    """Start regression tests."""

    input_path, output_path = deploy_helper(extract_input_and_strategy(parent_dir, input_filename, strategy_filename),
                                            result_path, timestamped_name(label, 'regression-results'), hostname)

    logging.info(f'Starting the job...')
    start_task('regression',
               input_path,
               output_path,
               n_jobs,
               n_cpus,
               input_suffix=Path(input_filename).suffix,
               hostname=hostname)

    if hostname:
        download_and_cleanup(hostname, output_path, result_path)

    logging.info('Regression test done!')


@app.command()
def mutate(label: Annotated[str, typer.Argument(help='Label for this group of mutations')],
           input_path: Annotated[str, typer.Argument(help='Path to the seed directory')],
           result_path: Annotated[str, typer.Argument(help='Path to the output directory')],
           n_times: Annotated[int, typer.Argument(help='Max number of mutations applied on given seeds')],
           n_jobs: Annotated[int, typer.Argument(help='Number of workers')],
           n_cpus: Annotated[int, typer.Argument(help='Number of CPUs')],
           seed: Annotated[str, typer.Option(help='Random seed')] = '',
           hostname: Annotated[Optional[str], typer.Option(help='Name of the remote server')] = None):
    """Start a mutation job."""

    input_path, output_path = deploy_helper(input_path, result_path, timestamped_name(label, 'mutants'), hostname)

    logging.info(f'Starting the job...')
    start_task('mutate', input_path, output_path, n_jobs, n_cpus, n_times=n_times, seed=seed, hostname=hostname)

    if hostname:
        download_and_cleanup(hostname, output_path, result_path)

    logging.info('Mutation done!')


@app.command()
def analyze(filepath: Annotated[str, typer.Argument(help='Path to the JSON file of tasks')],
            result_path: Annotated[str, typer.Argument(help='Path to the output directory')],
            n_jobs: Annotated[int, typer.Argument(help='Number of workers')],
            n_cpus: Annotated[int, typer.Argument(help='Number of CPUs')],
            hostname: Annotated[Optional[str], typer.Option(help='Name of the remote server')] = None):
    """Run a group of experiments defined in `filepath`."""

    _result_path = Path(result_path).absolute()
    _result_path.mkdir(parents=True)

    tasks = json.load(open(filepath))

    with Progress() as progress:
        input_sets = tasks['InputSets']
        configs = Configuration.from_dict(tasks['Configurations'])

        per_input_set = progress.add_task("[cyan]InputSets", total=len(input_sets))
        per_config = progress.add_task("[green]Configurations", total=len(configs))

        for name, info in input_sets.items():
            progress.update(per_config, completed=0)
            for config in configs:
                config.runner_maker(name, **info)(_result_path, n_jobs, n_cpus, hostname)
                progress.update(per_config, advance=1)
            progress.update(per_input_set, advance=1)


@app.command()
def update_image(hosts: Annotated[Optional[list[str]],
                                  typer.Option(help='Update docker images on remote hosts if given')] = None):
    """Build the Docker image. If `hosts` is provided, update remote hosts with the new image."""

    try:
        # Don't forward "docker build" output to logging
        subprocess.run('DOCKER_BUILDKIT=1 docker build \
            --network=host --platform=linux/amd64 -t verixmith .',
                       shell=True,
                       check=True)
    except subprocess.CalledProcessError:
        logging.fatal('Failed to build the docker image.')
        exit()

    if hosts:
        archive_file = f'{PREFIX}/verixmith.tar.gz'
        logging.info(f'Saving the image to {archive_file}...')
        log_subprocess_output(f'docker save verixmith:latest | gzip > {archive_file}', shell=True)

        for hostname in hosts:
            upload_to_remote(archive_file, hostname, f'{PREFIX}/')
            log_subprocess_output(f'ssh {hostname} docker load < {archive_file}', shell=True)

        remove(archive_file)


if __name__ == '__main__':
    app()
