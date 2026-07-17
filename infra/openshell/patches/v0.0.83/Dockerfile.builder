# syntax=docker/dockerfile:1

# The base is already part of the SIQ build cache and is pinned to the verified
# ARM64 manifest. Rust is fetched from the official, dated distribution URL.
FROM python:3.11.15-slim-bookworm@sha256:b18992999dbe963a45a8a4da40ac2b1975be1a776d939d098c647482bcad5cba

ARG SIQ_SUPERVISOR_PATCH_SHA256=unset
ARG SIQ_OPENSHELL_UPSTREAM_COMMIT=unset
LABEL ai.siq.openshell.supervisor-patch-sha256="$SIQ_SUPERVISOR_PATCH_SHA256" \
      ai.siq.openshell.upstream-commit="$SIQ_OPENSHELL_UPSTREAM_COMMIT" \
      ai.siq.openshell.rust-dist-sha256="094c9c36531911c5cc7dd6ab2d3069ab8dcd744d6239b0bda1387b243dfc391e"

ENV DEBIAN_FRONTEND=noninteractive \
    CARGO_TERM_COLOR=never \
    RUST_BACKTRACE=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        clang \
        cmake \
        build-essential \
        git \
        libclang-dev \
        libssl-dev \
        pkg-config \
        protobuf-compiler \
        python3 \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

ARG SIQ_OPENSHELL_BUILDER_DOCKERFILE_SHA256=unset
LABEL ai.siq.openshell.builder-dockerfile-sha256="$SIQ_OPENSHELL_BUILDER_DOCKERFILE_SHA256"

ADD --checksum=sha256:094c9c36531911c5cc7dd6ab2d3069ab8dcd744d6239b0bda1387b243dfc391e \
    https://static.rust-lang.org/dist/2026-04-16/rust-1.95.0-aarch64-unknown-linux-gnu.tar.xz \
    /tmp/rust.tar.xz

RUN mkdir /tmp/rust-dist \
    && tar -xJf /tmp/rust.tar.xz -C /tmp/rust-dist --strip-components=1 \
    && /tmp/rust-dist/install.sh --prefix=/usr/local --disable-ldconfig \
    && rustc --version | grep -F 'rustc 1.95.0' \
    && cargo --version | grep -F 'cargo 1.95.0' \
    && rm -rf /tmp/rust-dist /tmp/rust.tar.xz
