# This is designed to be run both locally and remotely.

# --- Mandatory arguments -- #
task_name=${1:?"Name of the task (default | regression | mutate)"}

input_archive_or_directory=${2:?"Verilog/SystemVerilog files as input"}
output_archive_or_directory=${3:?"Output path or archive file"}

n_jobs=${4:?"Number of workers"}
n_cpus=${5:?"Number of CPUs"}

# ----- Shared options ----- #
seed=${seed:-""}                # Initialize random seed (used by default and mutate)
max_time=${max_time:-"0"}       # Time limit
max_memory=${max_memory:-"90g"} # Memory limit

# -- Options for "default" - #
n_validations=${n_validations:-"0"}          # Number of validations applied to each input
source_node=${source_node:-"VerilogCircuit"} # Name of the source node in tv-graph
sink_node=${sink_node:-"SmtCircuit"}         # Name of the sink node in tv-graph
test_only=${test_only:-"False"}              # Disable full equivalence check

#  Options for "regression"  #
input_suffix=${input_suffix:-".v"} # Suffix of the input files

# -- Options for "mutate" -- #
n_times=${n_times:-"0"} # Max number of mutations applied on given seeds

log() {
  echo -e "$1"
}

if [ "$input_archive_or_directory" != "${input_archive_or_directory%.tar.gz}" ]; then
  input_dir=rtls
  log "***** Extracting the archive data... *****"
  mkdir -p $input_dir && tar xf $input_archive_or_directory --directory=$input_dir --warning=no-timestamp && rm $input_archive_or_directory
else
  input_dir=$input_archive_or_directory
fi

container_name=$(basename ${output_archive_or_directory%.tar.gz}) # remove the suffix
if [ "$output_archive_or_directory" != "${output_archive_or_directory%.tar.gz}" ]; then
  output_dir=results
else
  output_dir=$output_archive_or_directory
fi

if [ "$max_time" == "0" ]; then
  commands+=(sudo cron "&&")
else
  commands+=(timeout --preserve-status --signal=SIGKILL $max_time)
fi

if [ "$task_name" == "regression" ]; then
  commands+=("inv regression-test /app/verixmith/rtls --input-suffix="$input_suffix" --n-jobs=$n_jobs")
elif [ "$task_name" == "mutate" ]; then
  commands+=("inv mutate /app/verixmith/rtls /app/verixmith/results --n-times=$n_times --n-jobs=$n_jobs")
  if [ -n "$seed" ]; then
    commands+=(--seed="$seed")
  fi
else # default
  commands+=(inv batch-test /app/verixmith/rtls $source_node $sink_node $n_validations --n-jobs=$n_jobs)
  if [ "$test_only" == "True" ]; then
    commands+=(--test-only)
  fi
  if [ -n "$seed" ]; then
    commands+=(--seed="$seed")
  fi
fi

log ">>> ${commands[*]}"
mkdir -p $output_dir
docker run -i -t -d \
  --cpus="$n_cpus" \
  --memory=$max_memory \
  --name $container_name \
  --mount type=bind,source="$(realpath $input_dir)",target=/app/verixmith/rtls \
  --mount type=bind,source="$(realpath $output_dir)",target=/app/verixmith/results \
  verixmith:latest \
  sh -c \
  "sudo chmod -R a+w /app/verixmith/rtls /app/verixmith/results && ${commands[*]}"

docker wait $container_name
docker logs $container_name
docker rm $container_name

log "***** Collecting the results... *****"
sudo chown -R zyk:zyk $output_dir

if [ "$task_name" == "mutate" ]; then
  rm -rf $output_dir/mutation_errors
fi
rm -rf $output_dir/tmp*

if [ "$output_archive_or_directory" != "${output_archive_or_directory%.tar.gz}" ]; then
  tar czf $output_archive_or_directory --directory=$output_dir --warning=no-timestamp . && rm -r $output_dir
fi

if [ "$input_archive_or_directory" != "${input_archive_or_directory%.tar.gz}" ]; then
  rm -r $input_dir
fi
