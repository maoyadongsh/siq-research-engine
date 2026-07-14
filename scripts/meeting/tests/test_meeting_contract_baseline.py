from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


def _load_module():
    source = Path(__file__).resolve().parents[1] / "meeting_contract_baseline.py"
    spec = importlib.util.spec_from_file_location("meeting_contract_baseline_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _legacy_openapi(*, response_type: str = "string", include_meeting: bool = False) -> dict:
    document = {
        "openapi": "3.1.0",
        "paths": {
            "/api/chat/history": {
                "get": {
                    "operationId": "chat_history",
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {"type": "integer", "default": 20},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "legacy response",
                            "content": {
                                "application/json": {"schema": {"$ref": "#/components/schemas/LegacyResponse"}}
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "LegacyResponse": {
                    "type": "object",
                    "properties": {"value": {"type": response_type}},
                    "required": ["value"],
                }
            }
        },
    }
    if include_meeting:
        document["paths"]["/api/meetings/v1/sessions"] = {
            "post": {
                "operationId": "create_meeting",
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/MeetingCreate"}}}
                },
                "responses": {"201": {"description": "created"}},
            }
        }
        document["components"]["schemas"]["MeetingCreate"] = {
            "type": "object",
            "properties": {"title": {"type": "string"}},
        }
    return document


def _database(*, legacy_type: str = "VARCHAR", include_meeting: bool = False) -> dict:
    tables = {
        "users": {
            "columns": {
                "id": {
                    "type": "INTEGER",
                    "nullable": False,
                    "primary_key": True,
                    "default": None,
                },
                "name": {
                    "type": legacy_type,
                    "nullable": False,
                    "primary_key": False,
                    "default": None,
                },
            },
            "indexes": {
                "ix_users_name": {"columns": ["name"], "unique": False},
            },
        }
    }
    if include_meeting:
        tables["meeting_sessions"] = {
            "columns": {
                "id": {
                    "type": "VARCHAR(36)",
                    "nullable": False,
                    "primary_key": True,
                    "default": None,
                }
            },
            "indexes": {},
        }
    return tables


def _repo(tmp_path: Path, *, profile_text: str = "model: legacy\n") -> Path:
    root = tmp_path
    _write(root / "agents/hermes/profiles/siq_assistant/config.yaml", profile_text)
    _write(
        root / "infra/env/local.example",
        "SIQ_BACKEND_PORT=18081\nSIQ_OPTIONAL_FEATURE_ENABLED=0\n",
    )
    _write(
        root / "start_all.sh",
        "\n".join(
            [
                'BACKEND_PORT="${SIQ_BACKEND_PORT:-${BACKEND_PORT:-18081}}"',
                'require_free_port "$BACKEND_PORT" "FastAPI backend"',
                'wait_for_http "http://127.0.0.1:$BACKEND_PORT/health" "FastAPI backend"',
            ]
        )
        + "\n",
    )
    _write(
        root / "infra/systemd-user/siq-api.service",
        "[Unit]\nDescription=SIQ API\n[Service]\nExecStart=/usr/bin/bash %h/siq/start.sh --serve\n"
        "[Install]\nWantedBy=default.target\n",
    )
    return root


def _snapshot(module, root: Path, *, openapi: dict | None = None, database: dict | None = None):
    return module.build_snapshot(
        repo_root=root,
        source_commit="a" * 40,
        requested_ref="refs/heads/pre-meeting",
        source_kind="git-ref",
        openapi_document=openapi or _legacy_openapi(),
        database_contract=database or _database(),
    )


def test_capture_is_byte_stable_for_the_same_committed_contract(tmp_path):
    module = _load_module()
    snapshot = _snapshot(module, _repo(tmp_path))
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"

    module._write_json(first, snapshot)
    module._write_json(second, snapshot)

    assert first.read_bytes() == second.read_bytes()
    assert json.loads(first.read_text(encoding="utf-8"))["source"]["kind"] == "git-ref"


def test_profile_contract_ignores_generated_dist_directories(tmp_path):
    module = _load_module()
    root = _repo(tmp_path)
    baseline = module.hash_hermes_profiles(root)

    _write(
        root / "agents/hermes/profiles/siq_ic_shared/skills/due-diligence-analyst/dist/index.js",
        "export const generated = true;\n",
    )

    assert module.hash_hermes_profiles(root) == baseline


def test_framework_multi_method_operation_id_suffix_is_normalized():
    module = _load_module()
    first = _legacy_openapi()
    second = _legacy_openapi()
    for document, suffix in ((first, "delete"), (second, "post")):
        document["paths"]["/api/proxy/{path}"] = {
            method: {
                "operationId": f"proxy_api_proxy__path__{suffix}",
                "responses": {"200": {"description": "ok"}},
            }
            for method in ("get", "post", "delete")
        }

    first_contract = module.normalize_openapi(first)
    second_contract = module.normalize_openapi(second)

    assert first_contract == second_contract
    assert first_contract["operations"]["GET /api/proxy/{path}"]["operationId"].endswith("_<multi-method>")


def test_verify_allows_only_meeting_api_table_profile_and_runtime_additions(tmp_path):
    module = _load_module()
    baseline_root = _repo(tmp_path / "baseline")
    candidate_root = tmp_path / "candidate"
    shutil.copytree(baseline_root, candidate_root)
    _write(
        candidate_root / "agents/hermes/profiles/siq_meeting/config.yaml",
        "model: isolated-meeting-target\n",
    )
    with (candidate_root / "infra/env/local.example").open("a", encoding="utf-8") as handle:
        handle.write("SIQ_MEETING_SPEECH_PORT=8901\n")
    with (candidate_root / "start_all.sh").open("a", encoding="utf-8") as handle:
        handle.write('MEETING_PORT="${SIQ_MEETING_PORT:-${MEETING_PORT:-8901}}"\n')
        handle.write('require_free_port "$MEETING_PORT" "Meeting Speech"\n')

    baseline = _snapshot(module, baseline_root)
    candidate = _snapshot(
        module,
        candidate_root,
        openapi=_legacy_openapi(include_meeting=True),
        database=_database(include_meeting=True),
    )
    report = module.compare_snapshots(baseline, candidate)

    assert report["passed"] is True
    assert report["differences"] == []


def test_verify_blocks_legacy_openapi_database_and_index_drift_with_stable_paths(tmp_path):
    module = _load_module()
    root = _repo(tmp_path)
    baseline = _snapshot(module, root)
    changed_database = _database(legacy_type="TEXT")
    changed_database["users"]["indexes"]["ix_users_email"] = {
        "columns": ["name"],
        "unique": True,
    }
    candidate = _snapshot(
        module,
        root,
        openapi=_legacy_openapi(response_type="integer"),
        database=changed_database,
    )

    first = module.compare_snapshots(baseline, candidate)
    second = module.compare_snapshots(baseline, candidate)

    assert first == second
    assert first["passed"] is False
    assert {item["contract"] for item in first["differences"]} == {
        "legacy_database",
        "legacy_openapi",
    }
    assert any("LegacyResponse" in item["path"] for item in first["differences"])
    assert any("ix_users_email" in item["path"] for item in first["differences"])
    assert all("before" not in item or item.get("before_sha256") for item in first["differences"])


def test_verify_blocks_existing_hermes_profile_changes_and_nonmeeting_additions(tmp_path):
    module = _load_module()
    baseline_root = _repo(tmp_path / "baseline")
    candidate_root = tmp_path / "candidate"
    shutil.copytree(baseline_root, candidate_root)
    _write(
        candidate_root / "agents/hermes/profiles/siq_assistant/config.yaml",
        "model: changed-global-model\n",
    )
    _write(
        candidate_root / "agents/hermes/profiles/siq_unrelated/config.yaml",
        "model: nonmeeting-profile-secret-value\n",
    )

    report = module.compare_snapshots(
        _snapshot(module, baseline_root),
        _snapshot(module, candidate_root),
    )

    assert report["passed"] is False
    profile_differences = [item for item in report["differences"] if item["contract"] == "hermes_profiles"]
    assert [item["change"] for item in profile_differences] == ["changed", "added"]
    serialized = json.dumps(report)
    assert "changed-global-model" not in serialized
    assert "nonmeeting-profile-secret-value" not in serialized


def test_snapshot_filters_sensitive_examples_defaults_env_urls_and_profile_content(tmp_path):
    module = _load_module()
    root = _repo(tmp_path, profile_text="api_key: profile-super-secret\n")
    _write(
        root / "infra/env/local.example",
        "SIQ_SERVICE_TOKEN=env-super-secret\n"
        "SIQ_BACKEND_PORT=18081\n"
        "SIQ_SERVICE_HEALTH_URL=https://user:url-secret@health.internal/ready?token=query-secret\n",
    )
    openapi = _legacy_openapi()
    openapi["components"]["schemas"]["LegacyResponse"]["properties"]["api_token"] = {
        "type": "string",
        "default": "openapi-super-secret",
        "example": "openapi-example-secret",
    }
    openapi["paths"]["/api/chat/history"]["get"]["parameters"].append(
        {
            "name": "Authorization",
            "in": "header",
            "schema": {"type": "string", "default": "header-super-secret"},
        }
    )
    database = _database()
    database["users"]["columns"]["api_key"] = {
        "type": "VARCHAR",
        "nullable": True,
        "primary_key": False,
        "default": "database-super-secret",
    }

    snapshot = _snapshot(module, root, openapi=openapi, database=database)
    serialized = json.dumps(snapshot, sort_keys=True)

    for secret in (
        "profile-super-secret",
        "env-super-secret",
        "url-secret",
        "query-secret",
        "openapi-super-secret",
        "openapi-example-secret",
        "header-super-secret",
        "database-super-secret",
    ):
        assert secret not in serialized
    assert "https://health.internal/ready" in serialized
    token_schema = snapshot["legacy_openapi"]["components"]["schemas"]["LegacyResponse"]["properties"]["api_token"]
    assert token_schema == {"default": "<redacted>", "type": "string"}
    assert snapshot["legacy_database"]["tables"]["users"]["columns"]["api_key"]["default"] == "<redacted>"
    profile_hash = next(iter(snapshot["hermes_profiles"]["files"].values()))
    assert len(profile_hash) == 64
    assert set(profile_hash) <= set("0123456789abcdef")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_default_baseline_ref_uses_origin_merge_base_and_capture_rejects_worktree(tmp_path):
    module = _load_module()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "contract@example.invalid")
    _git(repo, "config", "user.name", "Contract Test")
    _write(repo / "contract.txt", "base\n")
    _git(repo, "add", "contract.txt")
    _git(repo, "commit", "-m", "base")
    base_commit = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/master", base_commit)
    _write(repo / "contract.txt", "candidate\n")
    _git(repo, "commit", "-am", "candidate")

    commit, label = module.default_baseline_ref(repo)

    assert commit == base_commit
    assert label == "merge-base(HEAD,origin/master)"
    assert (
        module.main(
            [
                "--repo-root",
                str(repo),
                "capture",
                "--source-ref",
                "WORKTREE",
                "--output",
                str(tmp_path / "forbidden.json"),
            ]
        )
        == 2
    )


def _approved_delta(module, baseline: dict, candidate: dict) -> dict:
    artifact = module.build_approved_delta(
        baseline=baseline,
        candidate=candidate,
        review_scope="Legacy contract changes listed in this exact artifact",
        justification="Reviewed compatibility changes required by the release candidate",
    )
    artifact["approval"] = {
        "status": module.APPROVAL_APPROVED,
        "reviewed_by": "release-reviewer",
    }
    return artifact


def _changed_candidate(module, root: Path, *, database_change: bool = False) -> dict:
    database = _database(legacy_type="TEXT") if database_change else _database()
    return _snapshot(
        module,
        root,
        openapi=_legacy_openapi(response_type="integer"),
        database=database,
    )


def test_approved_delta_exact_match_passes_and_pending_capture_is_not_approval(tmp_path):
    module = _load_module()
    root = _repo(tmp_path)
    baseline = _snapshot(module, root)
    candidate = _changed_candidate(module, root)
    artifact = module.build_approved_delta(
        baseline=baseline,
        candidate=candidate,
        review_scope="Exact legacy OpenAPI compatibility delta",
        justification="The response representation was explicitly reviewed",
    )

    assert artifact["approval"] == {
        "status": module.APPROVAL_PENDING,
        "reviewed_by": None,
    }
    assert artifact["reviewed_candidate_commit"] == candidate["source"]["commit"]
    assert artifact["candidate_contract_sha256"] == module._candidate_contract_digest(candidate)
    assert "candidate_commit" not in artifact
    assert module.compare_snapshots(baseline, candidate, approved_delta=artifact)["passed"] is False
    assert all(
        set(item) == module.DIFFERENCE_FIELDS and len(item["before_sha256"]) == 64 and len(item["after_sha256"]) == 64
        for item in artifact["differences"]
    )

    artifact["approval"] = {
        "status": module.APPROVAL_APPROVED,
        "reviewed_by": "contract-owner",
    }
    report = module.compare_snapshots(baseline, candidate, approved_delta=artifact)

    assert report["passed"] is True
    assert report["approved_delta_status"] == module.APPROVAL_APPROVED
    assert report["missing_differences"] == []
    assert report["unexpected_differences"] == []
    assert report["mismatched_differences"] == []


def test_approved_delta_reports_extra_and_missing_exact_differences(tmp_path):
    module = _load_module()
    root = _repo(tmp_path)
    baseline = _snapshot(module, root)
    openapi_candidate = _changed_candidate(module, root)
    combined_candidate = _changed_candidate(module, root, database_change=True)

    openapi_approval = _approved_delta(module, baseline, openapi_candidate)
    extra_report = module.compare_snapshots(
        baseline,
        combined_candidate,
        approved_delta=openapi_approval,
    )
    assert extra_report["passed"] is False
    assert extra_report["missing_differences"] == []
    assert extra_report["unexpected_differences"]
    assert {item["contract"] for item in extra_report["unexpected_differences"]} == {"legacy_database"}

    combined_approval = _approved_delta(module, baseline, combined_candidate)
    missing_report = module.compare_snapshots(
        baseline,
        openapi_candidate,
        approved_delta=combined_approval,
    )
    assert missing_report["passed"] is False
    assert missing_report["unexpected_differences"] == []
    assert missing_report["missing_differences"]
    assert {item["contract"] for item in missing_report["missing_differences"]} == {"legacy_database"}


def test_approved_delta_reports_digest_mismatch_and_absence_fails_closed(tmp_path):
    module = _load_module()
    root = _repo(tmp_path)
    baseline = _snapshot(module, root)
    candidate = _changed_candidate(module, root)
    artifact = _approved_delta(module, baseline, candidate)
    artifact["differences"][0]["after_sha256"] = "0" * 64

    mismatch = module.compare_snapshots(baseline, candidate, approved_delta=artifact)
    assert mismatch["passed"] is False
    assert mismatch["missing_differences"] == []
    assert mismatch["unexpected_differences"] == []
    assert mismatch["mismatched_differences"][0]["fields"] == ["after_sha256"]

    absent = module.compare_snapshots(baseline, candidate)
    assert absent["passed"] is False
    assert absent["approved_delta_status"] == "missing"
    assert absent["unexpected_differences"] == absent["differences"]


def test_approved_delta_rejects_baseline_schema_and_normalization_mismatches(tmp_path):
    module = _load_module()
    root = _repo(tmp_path)
    baseline = _snapshot(module, root)
    candidate = _changed_candidate(module, root)
    artifact = _approved_delta(module, baseline, candidate)

    malformed_artifacts = []
    for field, value in (
        ("schema_version", "siq.meeting.contract-approved-delta.v999"),
        ("contract_schema_version", "siq.meeting.contract-baseline.v999"),
        ("baseline_commit", "b" * 40),
        ("baseline_snapshot_sha256", "0" * 64),
        ("reviewed_candidate_commit", "not-a-commit"),
        ("candidate_contract_sha256", "not-a-sha256"),
    ):
        malformed = copy.deepcopy(artifact)
        malformed[field] = value
        malformed_artifacts.append(malformed)
    malformed = copy.deepcopy(artifact)
    malformed["normalization"]["excluded_api_prefixes"] = ["/api/*"]
    malformed_artifacts.append(malformed)

    for malformed in malformed_artifacts:
        with pytest.raises(module.ContractBaselineError):
            module.compare_snapshots(baseline, candidate, approved_delta=malformed)


def test_approved_delta_rejects_duplicate_unsorted_and_nonexact_rules(tmp_path):
    module = _load_module()
    root = _repo(tmp_path)
    baseline = _snapshot(module, root)
    candidate = _changed_candidate(module, root, database_change=True)
    artifact = _approved_delta(module, baseline, candidate)
    assert len(artifact["differences"]) >= 2

    duplicate = copy.deepcopy(artifact)
    duplicate["differences"].insert(1, copy.deepcopy(duplicate["differences"][0]))
    unsorted = copy.deepcopy(artifact)
    unsorted["differences"] = list(reversed(unsorted["differences"]))
    glob = copy.deepcopy(artifact)
    glob["differences"][0]["path"] += "/*"
    prefix_rule = copy.deepcopy(artifact)
    prefix_rule["differences"][0]["path_prefix"] = "/legacy"

    for malformed in (duplicate, unsorted, glob, prefix_rule):
        with pytest.raises(module.ContractBaselineError):
            module.compare_snapshots(baseline, candidate, approved_delta=malformed)


def test_approved_delta_capture_rejects_worktree_candidates(tmp_path):
    module = _load_module()
    root = _repo(tmp_path)
    baseline = _snapshot(module, root)
    candidate = _changed_candidate(module, root)
    candidate["source"]["kind"] = "worktree"

    with pytest.raises(module.ContractBaselineError, match="committed Git ref"):
        module.build_approved_delta(
            baseline=baseline,
            candidate=candidate,
            review_scope="Exact candidate delta",
            justification="Prepared for a later independent review",
        )


def test_approved_delta_survives_later_source_commit_when_contract_is_identical(tmp_path):
    module = _load_module()
    root = _repo(tmp_path)
    baseline = _snapshot(module, root)
    reviewed_candidate = _changed_candidate(module, root)
    artifact = _approved_delta(module, baseline, reviewed_candidate)
    later_candidate = copy.deepcopy(reviewed_candidate)
    later_candidate["source"] = {
        "commit": "b" * 40,
        "kind": "worktree",
        "requested_ref": "WORKTREE",
    }

    report = module.compare_snapshots(
        baseline,
        later_candidate,
        approved_delta=artifact,
    )

    assert report["passed"] is True
    assert report["candidate_commit"] == "b" * 40
    assert report["candidate_contract_match"] is True
    assert artifact["reviewed_candidate_commit"] == "a" * 40


def test_approved_delta_rejects_changed_candidate_contract_digest(tmp_path):
    module = _load_module()
    root = _repo(tmp_path)
    baseline = _snapshot(module, root)
    reviewed_candidate = _changed_candidate(module, root)
    artifact = _approved_delta(module, baseline, reviewed_candidate)
    changed_candidate = copy.deepcopy(reviewed_candidate)
    changed_candidate["runtime_metadata"]["evidence_kind"] = "changed-contract"

    report = module.compare_snapshots(
        baseline,
        changed_candidate,
        approved_delta=artifact,
    )

    assert report["passed"] is False
    assert report["candidate_contract_match"] is False
    assert report["candidate_contract_sha256"] != artifact["candidate_contract_sha256"]
    with pytest.raises(module.ContractBaselineError, match="candidate contract SHA-256"):
        module.validate_approved_delta(
            artifact,
            baseline=baseline,
            candidate=changed_candidate,
        )
