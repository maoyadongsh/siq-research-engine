import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    source = Path(__file__).resolve().parents[1] / "check_local_security_hygiene.py"
    spec = importlib.util.spec_from_file_location("check_local_security_hygiene_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _mkdir(path: Path, mode: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)


def _write_workflow(repo_root: Path, content: str) -> None:
    workflow = repo_root / ".github" / "workflows" / "market-postgres-release-gate.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(content, encoding="utf-8")


def _write_startup_guards(repo_root: Path) -> None:
    for relative in ("start_all.sh", "apps/api/start.sh"):
        path = repo_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(
                [
                    "SIQ_DEPLOYMENT_PROFILE",
                    "SIQ_UVICORN_RELOAD must not be enabled",
                    "FLASK_DEBUG must not be enabled",
                    "uvicorn_args+=(--reload)",
                    'uv run python -m uvicorn "${uvicorn_args[@]}"',
                    "",
                ]
            ),
            encoding="utf-8",
        )


def _write_container_security_config(repo_root: Path, module) -> None:
    for relative in module.SERVICE_DOCKERFILES:
        path = repo_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("FROM scratch\nUSER 10001\n", encoding="utf-8")

    compose = repo_root / module.COMPOSE_FILE
    compose.parent.mkdir(parents=True, exist_ok=True)
    compose.write_text(
        "services:\n"
        + "".join(f"  {service}:\n    image: example\n    user: \"10001:10001\"\n" for service in module.COMPOSE_SERVICE_USERS),
        encoding="utf-8",
    )

    workflow = repo_root / module.CI_WORKFLOW
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(
        "\n".join(relative.as_posix() for relative in module.SERVICE_DOCKERFILES) + "\n",
        encoding="utf-8",
    )


def _write_supervisor_config(repo_root: Path) -> None:
    supervisor = repo_root / "infra" / "supervisor" / "supervisord.conf"
    supervisor.parent.mkdir(parents=True, exist_ok=True)
    supervisor.write_text(
        "\n".join(
            [
                "[supervisord]",
                "logfile=/tmp/supervisord.log",
                "logfile_maxbytes=20MB",
                "logfile_backups=5",
                "",
                "[program:backend]",
                "stdout_logfile=var/logs/backend.out.log",
                "stdout_logfile_maxbytes=20MB",
                "stdout_logfile_backups=5",
                "stderr_logfile=var/logs/backend.err.log",
                "stderr_logfile_maxbytes=20MB",
                "stderr_logfile_backups=5",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_required_gitignore(repo_root: Path, module) -> None:
    (repo_root / ".gitignore").write_text(
        "\n".join(module.REQUIRED_RUNTIME_IGNORE_PATTERNS) + "\n",
        encoding="utf-8",
    )


def test_hygiene_passes_for_private_dirs_and_loopback_postgres_workflow(tmp_path, capsys):
    module = _load_module()
    for dirname in module.SENSITIVE_LOCAL_DIRS:
        _mkdir(tmp_path / dirname, 0o700)
    _write_workflow(
        tmp_path,
        """
services:
  postgres:
    env:
      POSTGRES_PASSWORD: secret
    ports:
      - "127.0.0.1:5432:5432"
""",
    )
    _write_startup_guards(tmp_path)
    _write_container_security_config(tmp_path, module)
    _write_supervisor_config(tmp_path)
    _write_required_gitignore(tmp_path, module)

    assert module.check_local_security_hygiene(tmp_path) == []
    assert module.main(["--repo-root", str(tmp_path)]) == 0
    assert "PASS local security hygiene" in capsys.readouterr().out


def test_hygiene_allows_loopback_nondefault_host_port(tmp_path):
    module = _load_module()
    _write_workflow(
        tmp_path,
        """
services:
  postgres:
    ports:
      - 127.0.0.1:15432:5432
""",
    )
    _write_startup_guards(tmp_path)
    _write_container_security_config(tmp_path, module)
    _write_supervisor_config(tmp_path)
    _write_required_gitignore(tmp_path, module)

    assert module.check_local_security_hygiene(tmp_path) == []


def test_workflow_scope_ignores_local_directory_permissions(tmp_path, capsys):
    module = _load_module()
    _mkdir(tmp_path / "data", 0o755)
    _write_workflow(
        tmp_path,
        """
services:
  postgres:
    env:
      POSTGRES_PASSWORD: secret
    ports:
      - 127.0.0.1:15432:5432
""",
    )
    _write_startup_guards(tmp_path)
    _write_container_security_config(tmp_path, module)
    _write_supervisor_config(tmp_path)
    _write_required_gitignore(tmp_path, module)

    assert module.check_local_security_hygiene(tmp_path, scope="workflow") == []
    assert module.main(["--repo-root", str(tmp_path), "--scope", "workflow"]) == 0
    assert "PASS local security hygiene" in capsys.readouterr().out


def test_local_dirs_scope_ignores_workflow_permissions(tmp_path):
    module = _load_module()
    _mkdir(tmp_path / "data", 0o700)
    _write_workflow(
        tmp_path,
        """
services:
  postgres:
    env:
      POSTGRES_HOST_AUTH_METHOD: trust
    ports:
      - 5432:5432
""",
    )
    _write_container_security_config(tmp_path, module)
    _write_supervisor_config(tmp_path)
    _write_required_gitignore(tmp_path, module)

    assert module.check_local_security_hygiene(tmp_path, scope="local-dirs") == []


def test_workflow_scope_fails_when_production_startup_guard_missing(tmp_path):
    module = _load_module()
    _write_workflow(
        tmp_path,
        """
services:
  postgres:
    env:
      POSTGRES_PASSWORD: secret
    ports:
      - 127.0.0.1:15432:5432
""",
    )
    _write_container_security_config(tmp_path, module)
    _write_supervisor_config(tmp_path)
    _write_required_gitignore(tmp_path, module)
    (tmp_path / "start_all.sh").write_text("uv run python -m uvicorn main:app --reload --host 0.0.0.0\n", encoding="utf-8")

    findings = module.check_local_security_hygiene(tmp_path, scope="workflow")

    assert [finding.code for finding in findings] == [
        "production_startup_guard_missing",
        "production_startup_hardcoded_dev_uvicorn",
    ]
    assert findings[0].path == "start_all.sh"


def test_hygiene_fails_for_world_accessible_dirs_and_postgres_workflow_risks(tmp_path, capsys):
    module = _load_module()
    _mkdir(tmp_path / "data", 0o755)
    _write_workflow(
        tmp_path,
        """
services:
  postgres:
    env:
      POSTGRES_HOST_AUTH_METHOD: trust
    ports:
      - 5432:5432
""",
    )
    _write_startup_guards(tmp_path)
    _write_container_security_config(tmp_path, module)
    _write_supervisor_config(tmp_path)
    _write_required_gitignore(tmp_path, module)

    findings = module.check_local_security_hygiene(tmp_path)
    assert [finding.code for finding in findings] == [
        "local_sensitive_dir_world_access",
        "postgres_trust_auth",
        "postgres_wide_5432_binding",
    ]
    assert findings[0].detail == "world-readable, world-executable permissions set (mode 0755)"

    assert module.main(["--repo-root", str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert "FAIL local security hygiene" in output
    assert ".github/workflows/market-postgres-release-gate.yml" in output


def test_local_dirs_scope_reports_sensitive_files_with_world_permissions(tmp_path):
    module = _load_module()
    _mkdir(tmp_path / "data", 0o700)
    public_file = tmp_path / "data" / "wiki" / "company.json"
    public_file.parent.mkdir(parents=True)
    public_file.write_text("{}", encoding="utf-8")
    public_file.chmod(0o644)
    private_file = tmp_path / "data" / "wiki" / "private.json"
    private_file.write_text("{}", encoding="utf-8")
    private_file.chmod(0o600)

    findings = module.check_local_security_hygiene(tmp_path, scope="local-dirs")

    assert [finding.code for finding in findings] == ["local_sensitive_file_world_access"]
    assert findings[0].path == "data/wiki/company.json"
    assert "world-readable" in findings[0].detail


def test_json_output_reports_findings_and_nonzero_exit(tmp_path, capsys):
    module = _load_module()
    _mkdir(tmp_path / "var", 0o701)
    _write_container_security_config(tmp_path, module)
    _write_supervisor_config(tmp_path)

    assert module.main(["--repo-root", str(tmp_path), "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False
    assert payload["findings"] == [
        {
            "code": "local_sensitive_dir_world_access",
            "path": "var",
            "detail": "world-executable permissions set (mode 0701)",
            "line": None,
        }
    ]


def test_workflow_scope_fails_for_container_security_regressions(tmp_path):
    module = _load_module()
    _write_workflow(
        tmp_path,
        """
services:
  postgres:
    env:
      POSTGRES_PASSWORD: secret
    ports:
      - 127.0.0.1:15432:5432
""",
    )
    _write_startup_guards(tmp_path)
    _write_container_security_config(tmp_path, module)
    _write_supervisor_config(tmp_path)
    _write_required_gitignore(tmp_path, module)

    (tmp_path / "services/market-report-finder/Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    compose = tmp_path / module.COMPOSE_FILE
    compose.write_text(
        compose.read_text(encoding="utf-8").replace(
            '  report-finder:\n    image: example\n    user: "10001:10001"\n',
            "  report-finder:\n    image: example\n",
        ),
        encoding="utf-8",
    )
    workflow = tmp_path / module.CI_WORKFLOW
    workflow.write_text(
        workflow.read_text(encoding="utf-8").replace("services/market-report-rules/Dockerfile\n", ""),
        encoding="utf-8",
    )

    findings = module.check_local_security_hygiene(tmp_path, scope="workflow")

    assert {finding.code for finding in findings} == {
        "service_dockerfile_missing_user",
        "compose_service_missing_user",
        "ci_hadolint_missing_service_dockerfile",
    }


def test_workflow_scope_fails_when_supervisor_log_rotation_is_missing(tmp_path):
    module = _load_module()
    _write_workflow(
        tmp_path,
        """
services:
  postgres:
    env:
      POSTGRES_PASSWORD: secret
    ports:
      - 127.0.0.1:15432:5432
""",
    )
    _write_startup_guards(tmp_path)
    _write_container_security_config(tmp_path, module)
    _write_required_gitignore(tmp_path, module)
    supervisor = tmp_path / module.SUPERVISOR_CONFIG
    supervisor.parent.mkdir(parents=True, exist_ok=True)
    supervisor.write_text(
        "\n".join(
            [
                "[program:backend]",
                "stdout_logfile=var/logs/backend.out.log",
                "",
            ]
        ),
        encoding="utf-8",
    )

    findings = module.check_local_security_hygiene(tmp_path, scope="workflow")

    assert [finding.code for finding in findings] == [
        "supervisor_log_rotation_missing",
        "supervisor_log_rotation_missing",
    ]
    assert "stdout_logfile_maxbytes" in findings[0].detail
    assert "stdout_logfile_backups" in findings[1].detail


def test_workflow_scope_fails_when_runtime_artifact_ignore_rules_are_missing(tmp_path):
    module = _load_module()
    _write_workflow(
        tmp_path,
        """
services:
  postgres:
    env:
      POSTGRES_PASSWORD: secret
    ports:
      - 127.0.0.1:15432:5432
""",
    )
    _write_startup_guards(tmp_path)
    _write_container_security_config(tmp_path, module)
    _write_supervisor_config(tmp_path)
    (tmp_path / ".gitignore").write_text("data/**\n", encoding="utf-8")

    findings = module.check_local_security_hygiene(tmp_path, scope="workflow")

    assert {finding.code for finding in findings} == {"runtime_artifact_ignore_missing"}
    missing = {finding.detail.rsplit(": ", 1)[-1] for finding in findings}
    assert "data/wiki/" in missing
    assert "data/postgres/" in missing
    assert "infra/env/*.env" in missing
