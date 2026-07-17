# SIQ OpenShell provider assets

This directory contains only secret-free provider definitions pinned to
OpenShell `0.0.83`. It does not contain provider values, generated gateway
state, or a materialized Hermes `auth.json`.

## Contents

- `manifest.json` binds stable provider instance names to profile files and
  credential environment keys.
- `profiles/*.yaml` are OpenShell `0.0.83` custom provider profiles. Every
  endpoint uses enforced REST method/path rules.
- `hermes/minimax-cn-auth-pool.template.json` preserves the two-entry Hermes
  MiniMax China pool using OpenShell placeholders, priority `0` then `10`.

The MiniMax profile keeps the current pool base URL unchanged and permits both
the canonical `/v1/messages` route and the current Hermes/Anthropic client
compatibility route `/v1/v1/messages`. This avoids introducing an OpenShell
deny while the host baseline is held stable; normalizing that base URL is a
separate Hermes configuration change and is not performed here.

The Tavily profile opts in to `request_body_credential_rewrite`. OpenShell
`0.0.83` buffers at most `262144` bytes (256 KiB) for this feature. Supported
UTF-8 JSON, form, and text requests at or below that boundary are rewritten;
larger or unresolved-placeholder requests are denied. Exa and the model APIs
keep credentials in request headers and do not enable body rewriting.

The reviewed SIQ web configuration continues to use the existing Tavily and
Exa `/search` calls without changing their request or response contract. The
profiles additionally make the providers' retrieval capabilities available:
Tavily search/extract/crawl/map/research and Exa search/contents/answer/context/
agent research. Only the asynchronous result trees use reviewed `/**` prefix
rules. File upload shapes, account administration, imports, monitors, websets,
arbitrary methods, and host-wide path access are not granted.

Tavily and Exa are approved retrieval processors, so their provider routes do
not use the generic unknown-domain 128 KiB threshold. This does not make them
generic publishing targets and does not provide a local file or raw byte input
surface. A sandbox that can read private data can still encode text in a legal
query; preventing that semantic channel requires the capability-separated
retrieval mode documented in the V0.6 taskbook.

## Validation and dry run

The default provisioning mode is non-mutating and does not read secret files
or credential environment variables:

```bash
python3 scripts/openshell/provision_siq_providers.py
```

Successful output contains only provider instance names and a deterministic
summary SHA-256. Server-side schema lint is also non-mutating, but requires the
isolated SIQ gateway to be reachable. Lint an unregistered profile by file:

```bash
scripts/openshell/run_cli.sh provider profile lint \
  --file infra/openshell/providers/profiles/siq-tavily-search.yaml
```

OpenShell `0.0.83` rejects a directory lint when that directory also contains
profiles already registered in the gateway. The provisioning command handles
incremental runs by linting only profiles that will be imported; existing
profiles are exported and compared in full, including `resource_version`,
before an update is allowed.

## Explicit provisioning

Provisioning is intentionally gated by `--apply` and an exact gateway
confirmation. The script requires OpenShell `0.0.83` and verifies that the
active registration is the local mTLS endpoint
`https://127.0.0.1:17671`. It also verifies that the gateway already has
`providers_v2_enabled=true`; it never changes gateway settings or attaches
providers to a sandbox.

Apply holds the same project maintenance lock used by sandbox lifecycle
commands and requires the gateway to contain no sandboxes. This prevents a
sandbox from starting between the quiescence check and the non-transactional
profile/provider updates. Stop and delete development sandboxes before a
provisioning run; a failed run can be repeated after correcting the reported
condition.

Secrets may come from the current process environment, one or more strict
dotenv files, and a Hermes `auth.json` containing exactly the reviewed two
MiniMax China pool entries. Secret files must be regular, non-symlink files
owned by the current user with no group or other permission bits (normally
mode `0600`). Values are passed to `openshell provider create/update` only in
the child process environment. CLI arguments contain bare credential key
names, never values, and child output is not forwarded to logs.

OpenShell `0.0.83` persists static provider credential values in the local
gateway database. For SIQ that file is
`var/openshell/gateway/siq-openshell-dev/openshell.db`: it must remain a
single-link, owner-only `0600` file under the Git-ignored runtime tree and is
never mounted into a sandbox. Gateway database backups have the same secret
classification. This version does not provide evidence of application-level
encryption at rest, so host filesystem permissions and disk encryption are
part of the credential boundary; sanitized artifacts must never contain the
database or its contents.

Example operator sequence (do not store these files in Git):

```bash
chmod 600 /restricted/siq-provider.env /restricted/hermes-auth.json
python3 scripts/openshell/provision_siq_providers.py \
  --apply \
  --confirm-gateway siq-openshell-dev \
  --secret-file /restricted/siq-provider.env \
  --minimax-auth-json /restricted/hermes-auth.json
```

An all-provider apply fails closed when any required credential is absent.
Use repeated `--provider NAME` arguments for a reviewed partial operation.
Enabling provider v2, sandbox attachment, and generation of the runtime
Hermes `auth.json` remain separate lifecycle steps so this command cannot
silently alter the sandbox or the current host runtime.
