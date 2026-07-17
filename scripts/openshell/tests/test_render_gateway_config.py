from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "render_gateway_config.py"
TEMPLATE = Path(__file__).resolve().parents[3] / "infra/openshell/gateway/siq-openshell-dev.toml.template"


def _module():
    spec = importlib.util.spec_from_file_location("siq_render_gateway_config_under_test", SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_gateway_config_is_isolated_pinned_and_mtls_only(tmp_path: Path) -> None:
    module = _module()
    content = module.render_config(TEMPLATE.read_bytes(), project_root=tmp_path)
    payload = module.validate_config(content, project_root=tmp_path)

    gateway = payload["openshell"]["gateway"]
    docker = payload["openshell"]["drivers"]["docker"]
    assert gateway["bind_address"] == "127.0.0.1:17671"
    assert gateway["mtls_auth"]["enabled"] is True
    assert gateway["auth"]["allow_unauthenticated_users"] is False
    assert docker["enable_bind_mounts"] is False
    assert docker["default_image"].endswith(":0.0.83")
    assert b"${" not in content


def test_gateway_config_rejects_bind_mount_enablement(tmp_path: Path) -> None:
    module = _module()
    template = TEMPLATE.read_bytes().replace(
        b"${SIQ_OPENSHELL_BIND_MOUNT_CONFIG}",
        b"enable_bind_mounts = true",
    )

    with pytest.raises(module.GatewayConfigError, match="bind mounts|contract"):
        module.render_config(template, project_root=tmp_path)


def test_gateway_config_renders_exact_attested_bind_contract(tmp_path: Path) -> None:
    module = _module()
    activation = {
        "contract": "siq_analysis_v2",
        "project_root": str(tmp_path.resolve()),
    }

    content = module.render_config(
        TEMPLATE.read_bytes(),
        project_root=tmp_path,
        activation=activation,
    )
    payload = module.validate_config(
        content,
        project_root=tmp_path,
        bind_mounts_enabled=True,
    )
    docker = payload["openshell"]["drivers"]["docker"]
    assert docker["enable_bind_mounts"] is True
    assert docker["bind_mount_contract"] == "siq_analysis_v2"
    assert docker["bind_mount_project_root"] == str(tmp_path.resolve())


def test_gateway_config_rejects_enabled_contract_with_wrong_root(tmp_path: Path) -> None:
    module = _module()
    activation = {
        "contract": "siq_analysis_v2",
        "project_root": "/tmp/not-siq",
    }

    with pytest.raises(module.GatewayConfigError, match="does not match"):
        module.render_config(
            TEMPLATE.read_bytes(),
            project_root=tmp_path,
            activation=activation,
        )
