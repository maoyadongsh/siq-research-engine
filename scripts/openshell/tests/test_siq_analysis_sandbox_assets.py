from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SANDBOX = ROOT / "infra" / "openshell" / "sandbox"


def test_dockerfile_pins_runtime_and_preserves_original_project_path() -> None:
    text = (SANDBOX / "Dockerfile").read_text(encoding="utf-8")

    assert "python@sha256:baf89808ec37adeaab83cec287adb4a2afa4a11c1d51e961c7ec737877e61af6" in text
    assert "node-v20.20.2-linux-arm64.tar.xz" in text
    assert "73093db209e4e9e09dd7d15a47aeaab1b74833830df03efa5f942a1122c5fa71" in text
    assert "COPY node-v20.20.2-linux-arm64.tar.xz /tmp/node.tar.xz" in text
    assert "ADD --checksum" not in text
    assert "fonts-noto-cjk" in text
    assert "ARG SIQ_SANDBOX_UID=1000" in text
    assert "ARG SIQ_SANDBOX_GID=1000" in text
    assert 'useradd --uid "$SIQ_SANDBOX_UID" --gid "$SIQ_SANDBOX_GID"' in text
    assert "USER sandbox:sandbox" in text
    assert "UV_PROJECT_ENVIRONMENT=/opt/siq/hermes/venv uv sync --frozen --no-dev" in text
    assert "HERMES_HOME=/tmp/hermes-build /opt/siq/hermes/venv/bin/hermes --version" in text
    assert "rm -rf /tmp/hermes-build" in text
    assert "COPY project/ /home/maoyd/siq-research-engine/" in text
    assert "HERMES_HOME=/home/maoyd/siq-research-engine/data/hermes/home/profiles/siq_analysis" in text
    assert "HERMES_AUTH_FILE=/sandbox/runtime-auth/auth.json" in text
    assert "ai.siq.hermes.integration-patch-sha256" in text
    assert "validate_provider_placeholders.py" in text
    assert "observe-entrypoint.sh" in text
    assert "runtime_state_lifecycle_smoke.py" in text
    assert "runtime_state_lifecycle_smoke.py probe_milvus_sandbox_boundary.py /opt/siq/" in text
    assert "/opt/siq/probe_milvus_sandbox_boundary.py" in text
    assert "COPY openshell-client/ /opt/siq/openshell-client/" in text
    assert "COPY siq-fetch /usr/local/bin/siq-fetch" in text
    assert "NO_PROXY=127.0.0.1,localhost,::1" in text
    assert "no_proxy=127.0.0.1,localhost,::1" in text
    assert "NO_PROXY=127.0.0.1,localhost,::1,host.openshell.internal" not in text


def test_sandbox_broker_clients_use_the_openshell_policy_proxy() -> None:
    milvus_probe = (ROOT / "scripts/openshell/probe_milvus_sandbox_boundary.py").read_text(
        encoding="utf-8"
    )
    security_probe = (ROOT / "scripts/openshell/probe_siq_analysis_sandbox.py").read_text(
        encoding="utf-8"
    )
    fetch_client = (ROOT / "scripts/openshell/siq_fetch.py").read_text(encoding="utf-8")
    lifecycle = (ROOT / "scripts/openshell/siq_analysis_lifecycle.py").read_text(encoding="utf-8")
    wide_pilot = (ROOT / "scripts/openshell/siq_analysis_wide_pilot.py").read_text(encoding="utf-8")

    assert "ProxyHandler({})" not in milvus_probe
    assert "ProxyHandler({})" not in security_probe
    assert "trust_env=True" in fetch_client
    for source in (lifecycle, wide_pilot):
        assert "NO_PROXY=127.0.0.1,localhost,::1,host.openshell.internal" not in source
        assert "no_proxy=127.0.0.1,localhost,::1,host.openshell.internal" not in source


def test_image_has_no_host_credentials_and_separates_control_from_runtime_state() -> None:
    dockerfile = (SANDBOX / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (SANDBOX / "entrypoint.sh").read_text(encoding="utf-8")
    dockerignore = (SANDBOX / ".dockerignore").read_text(encoding="utf-8")

    assert "auth.json" in dockerignore
    assert "**/.env" in dockerignore
    assert 'test ! -e "$HERMES_HOME/auth.json"' in dockerfile
    assert 'test ! -e "$HERMES_HOME/.env"' in dockerfile
    assert "HERMES_RUNTIME_HOME=/sandbox/siq-analysis-runtime-state" in dockerfile
    assert "/sandbox/siq-analysis-runtime-state" in dockerfile
    assert "/sandbox/runtime-lifecycle-smoke" not in dockerfile
    for runtime_file in (
        "response_store.db",
        "state.db",
        "gateway.pid",
        "gateway.lock",
        "gateway_state.json",
        "models_dev_cache.json",
        "processes.json",
    ):
        assert f'"$HERMES_HOME/{runtime_file}"' not in dockerfile
    assert "install -d -o sandbox -g sandbox -m 0700" in dockerfile
    assert '"$HERMES_HOME"' in dockerfile
    assert "Host credential files must not exist" in entrypoint
    assert "validate_placeholder_auth.py" in dockerfile
    assert "minimax-cn-auth-pool.template.json" in dockerfile
    assert 'install -m 0600 "$AUTH_TEMPLATE" "$EXPECTED_AUTH_FILE"' in entrypoint
    assert "API_SERVER_KEY" in entrypoint
    assert "SIQ_REQUIRE_OPENSHELL_PROVIDERS" in entrypoint
    assert "SIQ_RUNTIME_LIFECYCLE_SMOKE_ONLY" in entrypoint
    assert 'EXPECTED_RUNTIME_HOME="/sandbox/siq-analysis-runtime-state"' in entrypoint
    assert 'RUNTIME_LIFECYCLE_SMOKE_ROOT="$EXPECTED_RUNTIME_HOME"' in entrypoint
    assert '"${HERMES_RUNTIME_HOME:-}" != "$EXPECTED_RUNTIME_HOME"' in entrypoint
    assert entrypoint.index("SIQ_RUNTIME_LIFECYCLE_SMOKE_ONLY") < entrypoint.index(
        "A strong ephemeral API_SERVER_KEY"
    )

    observe_entrypoint = (SANDBOX / "observe-entrypoint.sh").read_text(encoding="utf-8")
    assert "NOT_PRODUCTION" in observe_entrypoint
    assert 'EXPECTED_HOME="$OBSERVE_ROOT/hermes-home"' in observe_entrypoint
    assert 'PROFILE_SOURCE="$PROJECT_ROOT/data/hermes/home/profiles/siq_analysis"' in observe_entrypoint
    assert 'cp -R "$PROFILE_SOURCE" "$EXPECTED_HOME"' in observe_entrypoint
    assert 'exec "$HERMES_BIN" gateway run' in observe_entrypoint


def test_prepare_context_uses_allowlists_scan_and_compiled_runtime_config() -> None:
    text = (ROOT / "scripts" / "openshell" / "prepare_siq_analysis_context.sh").read_text(encoding="utf-8")

    assert "prepare_hermes_poc.sh" in text
    assert "build_siq_analysis_runtime_config.py" in text
    assert "0001-runtime-auth-file-override.patch" in text
    assert "0002-runtime-state-home-override.patch" in text
    assert "0003-api-run-stop-quiescence.patch" in text
    assert "scripts/openshell/siq_fetch.py" in text
    assert "infra/openshell/egress/allowlist.json" in text
    assert "scripts/openshell/runtime_state_lifecycle_smoke.py" in text
    assert "scripts/openshell/probe_milvus_sandbox_boundary.py" in text
    assert '"$PROJECT_DIR/scripts/openshell"' not in text
    assert "scripts/openshell/broker_request_identity.py" in text
    assert '"$FIXTURE_DIR/observe-entrypoint.sh"' in text
    assert 'patch --directory="$CONTEXT_DIR/hermes-agent"' in text
    assert "Frozen Hermes integration patch was not materialized" in text
    assert text.count("check_mount_safety.py") >= 2
    assert "data/hermes/home/profiles/siq_analysis/config.yaml" in text
    assert "--exclude 'auth.json'" in text
    assert "contains_credentials" in text
    assert "contains_credential_placeholders_only" in text
    assert "contains_wiki_data" in text
    assert "NODE_ARCHIVE_SHA256" in text
    assert "--proto '=https' --tlsv1.2" in text


def test_dependency_versions_match_reviewed_host_capabilities() -> None:
    requirements = (SANDBOX / "requirements-siq-analysis.txt").read_text(encoding="utf-8").splitlines()

    assert requirements == [
        "aiohttp==3.13.3",
        "exa-py==2.12.0",
        "jsonschema==4.26.0",
        "psycopg2-binary==2.9.12",
    ]


def test_formal_image_smoke_is_non_networked_and_checks_auth_persistence() -> None:
    smoke = (ROOT / "scripts" / "openshell" / "smoke_siq_analysis_image.sh").read_text(encoding="utf-8")
    build = (ROOT / "scripts" / "openshell" / "build_siq_analysis_image.sh").read_text(encoding="utf-8")

    assert "--network none" in smoke
    assert "sandbox:sandbox" in smoke
    assert "validate_placeholder_auth.py" in smoke
    assert "write_credential_pool" in smoke
    assert "--runtime-lifecycle-only" in smoke
    assert "runtime_lifecycle_two_rounds" in smoke
    assert "runtime_lifecycle_directory_bind" in smoke
    assert "HERMES_RUNTIME_HOME" in smoke
    assert "/sandbox/siq-analysis-runtime-state" in smoke
    assert "gateway_pairing.PAIRING_DIR == runtime / \"platforms\" / \"pairing\"" in smoke
    assert "runtime_state_directory_bind_read_write" in smoke or "runtime-state bind" in smoke
    assert "--read-only" in smoke
    assert "--cap-drop ALL" in smoke
    assert "SIQ_RUNTIME_LIFECYCLE_SMOKE_ONLY=1" in smoke
    assert "candidate_image_directory_bind_without_openshell_policy_or_gateway" in smoke
    assert "formal_runtime_directory_bind_requires_live_sandbox_evidence" in smoke
    assert "sqlite_sidecars_are_not_file_bind_mounts" in smoke
    assert "gateway_metadata_parent_allows_atomic_replace" in smoke
    assert smoke.index("SIQ_RUNTIME_LIFECYCLE_SMOKE_ONLY=1") < smoke.index("docker run -d")
    assert "current-image.json" in smoke
    assert "siq.openshell.candidate_image.v1" in build
    assert 'user="$(docker image inspect' in build
