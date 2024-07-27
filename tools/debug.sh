#!/usr/bin/env bash
bug_directory=${1:?"directory name missing"}

# NOTE: It is necessary to create this directory before mount operation.
# Otherwise Docker on Windows will create it with root privilege, causing unexpected errors.
mkdir -p results

docker run -it --rm \
  --name $(date +%m%d-%H%M%S)-verixmith-debug \
  --mount type=bind,source="$(pwd)"/results,target=/app/verixmith/results \
  --mount type=bind,source="$(pwd)"/core,target=/app/verixmith/core \
  --mount type=bind,source="$(pwd)"/tasks.py,target=/app/verixmith/tasks.py \
  --mount type=bind,source=$bug_directory,target=/app/verixmith/bugs \
  verixmith:latest bash
