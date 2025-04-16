# syntax=docker/dockerfile:1

# Build Yosys from source
FROM --platform=linux/amd64 python:3.12.1-slim-bookworm AS yosys_base

RUN apt-get update && apt-get install -y \
    build-essential \
    clang \
    bison \
    flex \
    libreadline-dev \
    gawk \
    tcl-dev \
    libffi-dev \
    git \
    graphviz \
    xdot \
    pkg-config \
    libboost-system-dev \
    libboost-python-dev \
    libboost-filesystem-dev \
    zlib1g-dev \
    curl \
    jq \
    tar \
    wget \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/YosysHQ/yosys.git /tmp/yosys && \
    cd /tmp/yosys && \
    git checkout 171577f909cd0ecc33d879a8925d70f6c9ca8f1e && \
    make config-clang && \
    make && \
    make install
# Install Synlig, a SystemVerilog and UHDM front end plugin for Yosys
RUN curl https://api.github.com/repos/chipsalliance/synlig/releases/147437203 \
    | jq .assets[1] | grep "browser_download_url" | grep -Eo 'https://[^\"]*' \
    | xargs wget -O - | tar -xz && ./install_plugin.sh
RUN chmod o+r /usr/local/share/yosys/plugins/systemverilog.so


# Build sv2v from source
FROM --platform=linux/amd64 haskell:slim-bookworm AS sv2v_base

RUN git clone https://github.com/zachjs/sv2v.git /tmp/sv2v && \
    cd /tmp/sv2v && \
    git checkout v0.0.13 && \
    make


# Build Verilator from source
FROM --platform=linux/amd64 python:3.12.1-slim-bookworm AS verilator_base

RUN apt-get update && apt-get install --no-install-recommends -y \
    make \
    autoconf \
    bison \
    g++ \
    ccache \
    flex \
    libfl-dev \
    libgoogle-perftools-dev \
    perl \
    perl-doc \
    && rm -rf /var/lib/apt/lists/*

COPY dependencies/verilator /tmp/verilator/

RUN cd /tmp/verilator && \
    autoconf && \
    ./configure && \
    make && \
    make install && \
    cd .. && \
    rm -rf verilator


# Build Python packages
FROM --platform=linux/amd64 python:3.12.1-slim-bookworm AS python_packages

COPY requirements.txt /tmp/requirements.txt

RUN apt-get update && apt-get install --no-install-recommends -y \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && pip wheel --wheel-dir=/tmp/wheelhouse -r /tmp/requirements.txt \
    && pip install --no-index --find-links=/tmp/wheelhouse tree-sitter

ADD https://api.github.com/repos/tree-sitter/tree-sitter-verilog/git/refs/heads/master /tmp/versions/tree-sitter-verilog.json
# Download and build the tree-sitter language implementation for Verilog
RUN git clone https://github.com/tree-sitter/tree-sitter-verilog.git /tmp/tree-sitter-verilog/ && \
    python3 -c \
    "from tree_sitter import Language; Language.build_library('/tmp/tree-sitter.so', ['/tmp/tree-sitter-verilog'])" \
    rm -rf /tmp/tree-sitter-verilog


# Build KLEE from source
FROM ghcr.io/klee/llvm:130_O_D_A_ubuntu_jammy-20230126 as llvm_base
FROM ghcr.io/klee/gtest:1.11.0_ubuntu_jammy-20230126 as gtest_base
FROM ghcr.io/klee/uclibc:klee_uclibc_v1.3_130_ubuntu_jammy-20230126 as uclibc_base
FROM ghcr.io/klee/tcmalloc:2.9.1_ubuntu_jammy-20230126 as tcmalloc_base
FROM ghcr.io/klee/stp:2.3.3_ubuntu_jammy-20230126 as stp_base
FROM ghcr.io/klee/z3:4.8.15_ubuntu_jammy-20230126 as z3_base
FROM ghcr.io/klee/libcxx:130_ubuntu_jammy-20230126 as libcxx_base
FROM ghcr.io/klee/sqlite:3400100_ubuntu_jammy-20230126 as sqlite3_base
FROM llvm_base as klee_base
COPY --from=gtest_base /tmp /tmp/
COPY --from=uclibc_base /tmp /tmp/
COPY --from=tcmalloc_base /tmp /tmp/
COPY --from=stp_base /tmp /tmp/
COPY --from=z3_base /tmp /tmp/
COPY --from=libcxx_base /tmp /tmp/
COPY --from=sqlite3_base /tmp /tmp/
ENV COVERAGE=0
ENV USE_TCMALLOC=1
ENV BASE=/tmp
ENV LLVM_VERSION=13.0
ENV ENABLE_DOXYGEN=1
ENV ENABLE_OPTIMIZED=1
ENV ENABLE_DEBUG=1
ENV DISABLE_ASSERTIONS=0
ENV REQUIRES_RTTI=0
ENV SOLVERS=STP:Z3
ENV GTEST_VERSION=1.11.0
ENV UCLIBC_VERSION=klee_uclibc_v1.3
ENV TCMALLOC_VERSION=2.9.1
ENV SANITIZER_BUILD=
ENV STP_VERSION=2.3.3
ENV MINISAT_VERSION=master
ENV Z3_VERSION=4.8.15
ENV USE_LIBCXX=1
ENV KLEE_RUNTIME_BUILD="Debug+Asserts"
ENV SQLITE_VERSION=3400100
ENV CMAKE_POLICY_VERSION_MINIMUM=3.5
LABEL maintainer="KLEE Developers"

# Create ``klee`` user for container with password ``klee``.
# and give it password-less sudo access (temporarily so we can use the CI scripts)
RUN apt update && \
    DEBIAN_FRONTEND=noninteractive apt -y --no-install-recommends install \
    sudo \
    file \
    python3-dateutil \
    git \
    ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    useradd -m klee && \
    echo klee:klee | chpasswd && \
    echo 'klee  ALL=(root) NOPASSWD: ALL' >> /etc/sudoers

USER klee
WORKDIR /home/klee
# Build and set klee user to be owner
COPY --chown=klee:klee dependencies/klee.patch /tmp/
RUN git clone -b v3.1 https://github.com/klee/klee.git /tmp/klee_src/ && \
    cd /tmp/klee_src && \
    git apply /tmp/klee.patch && \
    scripts/build/build.sh --debug --install-system-deps klee && \
    sudo rm -rf /var/lib/apt/lists/* && \
    # Clean up unnecessary files (to reduce image size)
    sudo rm -rf /tmp/llvm-130-install_O_D_A/**/*.a && \
    sudo rm -rf /tmp/klee_build130stp_z3/unittests/*


# Finally
FROM --platform=linux/amd64 python:3.12.1-slim-bookworm

# Add Verilator binaries to system binary folder
COPY --from=verilator_base /usr/local/bin/verilator* /usr/local/bin/
# Add Verilator header files to system standard include folder
COPY --from=verilator_base /usr/local/share/verilator /usr/local/share/verilator

# Necessary files for CCache
COPY --from=verilator_base /usr/bin/ccache /usr/local/bin/
COPY --from=verilator_base /lib/x86_64-linux-gnu/libhiredis.so.0.14 /lib/x86_64-linux-gnu/libhiredis.so.0.14

# Copy sv2v binary to system binary folder
COPY --from=sv2v_base /tmp/sv2v/bin/sv2v /usr/local/bin/

# KLEE dependencies
COPY --from=klee_base /tmp/klee-uclibc-130 /tmp/klee-uclibc-130/
COPY --from=klee_base /tmp/klee_build130stp_z3 /tmp/klee_build130stp_z3/
COPY --from=klee_base /tmp/libc++-install-130 /tmp/libc++-install-130/
COPY --from=klee_base /tmp/llvm-130-install_O_D_A /tmp/llvm-130-install_O_D_A/
COPY --from=klee_base /tmp/minisat-install /tmp/minisat-install/
COPY --from=klee_base /tmp/sqlite-amalgamation-3400100 /tmp/sqlite-amalgamation-3400100/
COPY --from=klee_base /tmp/stp-2.3.3-install /tmp/stp-2.3.3-install/
COPY --from=klee_base /tmp/tcmalloc-install-2.9.1 /tmp/tcmalloc-install-2.9.1/
COPY --from=klee_base /tmp/z3-4.8.15-install /tmp/z3-4.8.15-install/
# Add KLEE header files to system standard include folder
COPY --from=klee_base /tmp/klee_src/include/klee /usr/include/klee/

# Copy Yosys executables and headers
COPY --from=yosys_base /usr/local/bin/yosys* /usr/local/bin/
COPY --from=yosys_base /usr/local/share/yosys /usr/local/share/yosys/

COPY --from=python_packages /tmp/wheelhouse /tmp/wheelhouse/
COPY --from=python_packages /tmp/tree-sitter.so /tmp/tree-sitter.so

COPY requirements.txt /tmp/requirements.txt

ENV LLVM_COMPILER=clang
ENV LLVM_COMPILER_PATH=/tmp/llvm-130-install_O_D_A/bin

RUN apt-get update && apt-get install --no-install-recommends -y \
    binutils \
    cron \
    file \
    iverilog \
    libc6-dev \
    libtcl8.6 \
    make \
    perl \
    procps \
    sudo \
    && rm -rf /var/lib/apt/lists/*

RUN adduser --disabled-password verixmith && \
    echo 'verixmith ALL=(root) NOPASSWD: ALL' >> /etc/sudoers
USER verixmith

RUN pip install --no-index --find-links=/tmp/wheelhouse -r /tmp/requirements.txt

ENV VERIXMITH_ROOT=/app/verixmith
WORKDIR ${VERIXMITH_ROOT}

# Provide source code of this project
COPY core ${VERIXMITH_ROOT}/core/
COPY tasks.py ${VERIXMITH_ROOT}/tasks.py

ENV PATH="${PATH}:/tmp/llvm-130-install_O_D_A/bin:/tmp/klee_build130stp_z3/bin:/home/verixmith/.local/bin"

# Files needed by ld (part of libgcc-11-dev)
# Reference: http://tolik1967.azurewebsites.net/clang_no_gcc.html
COPY --from=klee_base /usr/lib/gcc/x86_64-linux-gnu/11/crt*.o /usr/lib/gcc/x86_64-linux-gnu/11/
COPY --from=klee_base /usr/lib/gcc/x86_64-linux-gnu/11/libgcc*.a /usr/lib/gcc/x86_64-linux-gnu/11/
COPY --from=klee_base /usr/lib/gcc/x86_64-linux-gnu/11/libgcc_s.so /usr/lib/gcc/x86_64-linux-gnu/11/

# Use LLVM C++ headers and libraries
ENV CXXFLAGS=-I/tmp/llvm-130-install_O_D_A/include/c++/v1
ENV LDFLAGS="-nodefaultlibs -lc++ -lc++abi -lm -lc -lgcc_s -lgcc"
ENV LD_LIBRARY_PATH=/tmp/llvm-130-install_O_D_A/lib

# Reference: https://github.com/Ekito/docker-cron/issues/4
ADD tools/crontab /tmp/crontab
RUN crontab /tmp/crontab

CMD ["/bin/bash"]