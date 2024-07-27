#!/usr/bin/env bash

# Enable Docker BuildKit
export DOCKER_BUILDKIT=1

docker build --network=host --platform=linux/amd64 \
    -f dependencies/verismith/verismith.Dockerfile \
    -t verismith \
    .
