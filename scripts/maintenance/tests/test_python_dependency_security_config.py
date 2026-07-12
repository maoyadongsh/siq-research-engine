import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_toml(relative: str) -> dict:
    return tomllib.loads((REPO_ROOT / relative).read_text(encoding="utf-8"))


def _dependency_specs(pyproject: dict) -> dict[str, str]:
    specs: dict[str, str] = {}
    for raw in pyproject["project"]["dependencies"]:
        name = raw.split("[", 1)[0].split(";", 1)[0].split("<", 1)[0].split(">", 1)[0].split("=", 1)[0].strip()
        specs[name.lower()] = raw
    return specs


def _locked_version(lock: dict, package_name: str) -> str:
    for package in lock["package"]:
        if package["name"] == package_name:
            return str(package["version"])
    raise AssertionError(f"missing locked package: {package_name}")


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split(".") if part.isdigit())


def _metadata_spec(lock: dict, project_name: str, dependency_name: str) -> str:
    for package in lock["package"]:
        if package["name"] != project_name:
            continue
        for requirement in package.get("metadata", {}).get("requires-dist", []):
            if requirement["name"] == dependency_name:
                return str(requirement.get("specifier", ""))
    raise AssertionError(f"missing project requirement: {project_name} -> {dependency_name}")


def test_python_service_dependency_security_floors_are_pinned():
    projects = {
        "apps/api": {
            "package": "siq-assistant-backend",
            "floors": {
                "fastapi": ">=0.136.1",
                "starlette": ">=1.3.1",
                "idna": ">=3.15",
            },
        },
        "services/market-report-finder": {
            "package": "market-report-finder-service",
            "floors": {
                "fastapi": ">=0.136.1",
                "starlette": ">=1.3.1",
                "idna": ">=3.15",
                "pydantic-settings": ">=2.14.2",
            },
        },
        "services/market-report-rules": {
            "package": "market-report-rules-service",
            "floors": {
                "fastapi": ">=0.136.1",
                "starlette": ">=1.3.1",
                "idna": ">=3.15",
            },
        },
    }

    for relative, config in projects.items():
        pyproject = _load_toml(f"{relative}/pyproject.toml")
        lock = _load_toml(f"{relative}/uv.lock")
        dependency_specs = _dependency_specs(pyproject)

        for dependency, floor in config["floors"].items():
            assert floor in dependency_specs[dependency]
            assert floor in _metadata_spec(lock, config["package"], dependency)

        assert _version_tuple(_locked_version(lock, "fastapi")) >= (0, 136, 1)
        assert _version_tuple(_locked_version(lock, "starlette")) >= (1, 3, 1)
        assert _version_tuple(_locked_version(lock, "idna")) >= (3, 15)
        if "pydantic-settings" in config["floors"]:
            assert _version_tuple(_locked_version(lock, "pydantic-settings")) >= (2, 14, 2)


def test_ci_blocks_high_and_critical_security_findings_and_audits_locked_python_deps():
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "severity: HIGH,CRITICAL" in workflow
    assert "severity: CRITICAL" not in workflow
    assert "pip-audit==2.10.1" in workflow
    assert "scripts/maintenance/check_python_dependency_audit.py" in workflow
    assert "--require-pip-audit" in workflow
    assert "--block-all-vulnerabilities" in workflow
    assert "services/market-report-finder" in workflow
    assert "services/market-report-rules" in workflow
    assert "apps/api" in workflow
