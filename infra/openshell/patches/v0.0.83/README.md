# SIQ OpenShell v0.0.83 patches

Upstream base:

- repository: `NVIDIA/OpenShell`
- tag: `v0.0.83`
- commit: `e3d26dd3ae0dee247bbc5db368545832757ac493`
- original `landlock.rs` SHA-256:
  `2c2305fabdd66a42a6c2c5969dc38a9054d42e8978f09d845f62a17264ac1aa0`
- original ARM64 supervisor SHA-256:
  `d94630658eb1e62090281160db7cdc542c8cf6667d0c11ff7d9084251f86cfd6`

`0001-landlock-mask-file-access.patch` fixes a fail-closed startup bug in the
v0.0.83 supervisor. The upstream code passes directory-only Landlock rights to
every path, including `/dev/urandom` and `/dev/null`. In
`hard_requirement` mode, `landlock 0.4.4` rejects those file rules before the
kernel sees them.

The patch inspects the already-open `O_PATH` file descriptor. Directory rules
keep the upstream mask; non-directory rules are intersected with
`AccessFs::from_file(ABI::V2)`. It does not broaden `/dev`, does not change
network behavior, and does not downgrade `hard_requirement` to `best_effort`.

The patch also adds regression tests for directories, regular files,
`/dev/null`, and `/dev/urandom`. Build and install it with:

```bash
scripts/openshell/build_patched_supervisor.sh
```

The builder uses the pinned ARM64 manifest for
`python:3.11.15-slim-bookworm` and installs Rust 1.95.0 from the official
dated distribution archive. The archive SHA-256 is
`094c9c36531911c5cc7dd6ab2d3069ab8dcd744d6239b0bda1387b243dfc391e`.
The resulting builder image is labelled with that digest, the OpenShell
commit, and this patch digest; the build script verifies all three before
executing Cargo.

`0002-siq-strict-bind-mount-contract.patch` adds the gateway-side
`siq_analysis_v1` bind-mount contract. When configured, the Docker driver
accepts either no host mounts or exactly the fixed 12-mount SIQ plan: one
read-only Wiki root, one same-path task `analysis` directory, and the ten
read-write files/directories from a single Hermes runtime snapshot. It rejects
project-root, project-external, symlinked, `..`, Docker socket, TLS/control
state, arbitrary volume/tmpfs, mixed-run and mode/target-confused requests.

The gateway uses a metadata-only child of the verified supervisor builder and
compiles the Z3 policy prover with Cargo's `bundled-z3` feature. The resulting
gateway must not depend on a host `libz3.so`. Build and install it with:

```bash
scripts/openshell/build_patched_gateway.sh
```

The strict contract remains inactive until the project gateway template sets
all three fields together: `enable_bind_mounts`, `bind_mount_contract`, and
`bind_mount_project_root`.

Source, Cargo cache, build output, upstream backup and the executable remain
under ignored `var/openshell/`. The patch and its non-sensitive provenance are
committed for contest review and reproducibility.
