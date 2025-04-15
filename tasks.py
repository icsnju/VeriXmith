import random
from pathlib import Path

from invoke.collection import Collection
from invoke.tasks import task

import core.api
from core.thirdparty import miscellaneous, verilator, yosys

core.api.set_result_dir(Path.cwd() / 'failures')


@task
def replay(c, hdl_file, json_file):
    core.api.replay(Path(hdl_file), Path(json_file))


@task
def batch_test(c, rtl_dir, source_type, sink_type, n_samples, test_only=False, n_jobs=1, seed=None):
    random.seed(seed)
    core.api.run_validation(core.api.sample_compilation_space(Path(rtl_dir), source_type, sink_type, int(n_samples)),
                            test_only=test_only,
                            n_jobs=n_jobs)


@task
def regression_test(c, dir_name, input_suffix='.v', n_jobs=1):
    core.api.regression_test(Path(dir_name), n_jobs, input_suffix)


@task
def mutate(c, seed_dir, output_dir, n_times=0, n_jobs=1, debug=False, seed=None):
    random.seed(seed)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    core.api.run_mutation(Path(seed_dir), output_dir, n_times, n_jobs, debug)


namespace = Collection(replay, batch_test, regression_test, mutate, yosys, verilator, miscellaneous)

# display output when calling by `inv` command
namespace.configure({'run': {'hide': False, 'echo': True}})
