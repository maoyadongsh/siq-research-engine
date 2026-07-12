import importlib.util
from pathlib import Path

import pytest


def load_module():
    path = Path(__file__).resolve().parents[1] / "run_production_tls_proxy_smoke.py"
    spec = importlib.util.spec_from_file_location("production_tls_proxy_smoke", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_tls_environment_enables_secure_cookie_and_exact_https_origins():
    module = load_module()

    env = module.tls_smoke_environment(
        {"SIQ_AUTH_COOKIE_SECURE": "0", "SIQ_CORS_ALLOW_ORIGINS": "http://old"},
        "https://127.0.0.1:24443",
    )

    assert env["SIQ_AUTH_COOKIE_SECURE"] == "1"
    assert env["SIQ_AUTH_COOKIE_SAMESITE"] == "lax"
    assert env["SIQ_CORS_ALLOW_ORIGINS"] == "https://127.0.0.1:24443"
    assert env["SIQ_AUTH_CSRF_ALLOWED_ORIGINS"] == "https://127.0.0.1:24443"


def test_tls_proxy_config_is_test_local_and_forwards_https_boundary():
    module = load_module()

    config = module.tls_proxy_config(listen_port=24443, upstream_port=25173)

    assert "listen 127.0.0.1:24443 ssl;" in config
    assert "proxy_pass http://127.0.0.1:25173;" in config
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in config
    assert "proxy_set_header Host $http_host;" in config
    assert "proxy_set_header X-Forwarded-Proto https;" in config
    assert "ssl_certificate /etc/nginx/tls/cert.pem;" in config
    assert "fastcgi_temp_path /tmp/fastcgi;" in config
    assert "uwsgi_temp_path /tmp/uwsgi;" in config
    assert "scgi_temp_path /tmp/scgi;" in config


def complete_report():
    return {
        "https_login_status": 200,
        "https_cookie_authenticated_status": 200,
        "https_csrf_missing_status": 403,
        "https_csrf_wrong_origin_status": 403,
        "https_csrf_valid_status": 200,
        "http_cookiejar_status": 401,
        "access_cookie_secure": True,
        "access_cookie_httponly": True,
        "csrf_cookie_secure": True,
        "csrf_cookie_httponly": False,
        "cookie_paths_are_root": True,
        "logout_access_cookie_cleared": True,
        "logout_csrf_cookie_cleared": True,
        "temporary_self_signed_certificate": True,
        "tls_proxy_read_only": True,
        "tls_proxy_capabilities_dropped": True,
        "production_compose_modified": False,
    }


def test_tls_contract_accepts_https_secure_cookie_csrf_boundary():
    load_module().assert_tls_contract(complete_report())


def test_tls_contract_rejects_http_cookie_replay():
    module = load_module()
    report = complete_report()
    report["http_cookiejar_status"] = 200

    with pytest.raises(AssertionError, match="http_cookiejar_status"):
        module.assert_tls_contract(report)


def test_tls_smoke_does_not_add_self_signed_certificate_to_production_compose():
    repo_root = Path(__file__).resolve().parents[3]
    compose = (repo_root / "infra/docker/docker-compose.yml").read_text(encoding="utf-8")

    assert "self-signed" not in compose.lower()
    assert "cert.pem" not in compose
    assert "key.pem" not in compose
