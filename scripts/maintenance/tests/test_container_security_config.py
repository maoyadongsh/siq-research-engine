import json
import subprocess
from pathlib import Path

SERVICE_DOCKERFILES = (
    "apps/api/Dockerfile",
    "apps/web/Dockerfile",
    "apps/pdf-parser/Dockerfile",
    "apps/document-parser/Dockerfile",
    "services/market-report-finder/Dockerfile",
    "services/market-report-rules/Dockerfile",
)


def test_service_dockerfiles_run_as_non_root_users():
    repo_root = Path(__file__).resolve().parents[3]

    for relative in SERVICE_DOCKERFILES:
        text = (repo_root / relative).read_text(encoding="utf-8")
        assert "\nUSER " in text, relative
        assert "\nUSER root" not in text, relative


def test_root_dockerignore_excludes_local_secret_sources():
    repo_root = Path(__file__).resolve().parents[3]
    patterns = {
        line.strip()
        for line in (repo_root / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert "env/" in patterns
    assert "**/env/" in patterns
    assert ".env" in patterns
    assert "**/.env" in patterns
    assert "*.env" in patterns
    assert "**/*.env" in patterns


def test_market_report_services_have_explicit_compose_users():
    repo_root = Path(__file__).resolve().parents[3]
    compose = (repo_root / "infra/docker/docker-compose.yml").read_text(encoding="utf-8")

    assert "report-finder:\n    build:" in compose
    assert "market-report-finder:\n    profiles:" in compose
    assert compose.count('user: "10001:10001"') >= 5
    assert "report-finder:\n    build:\n      context: ../../services/market-report-finder\n      dockerfile: Dockerfile\n    user: \"10001:10001\"" in compose
    assert "market-report-finder:\n    profiles: [\"external-services\"]\n    build:\n      context: ../../services/market-report-finder\n      dockerfile: Dockerfile\n    user: \"10001:10001\"" in compose


def test_market_report_services_fail_closed_in_compose():
    repo_root = Path(__file__).resolve().parents[3]
    compose = (repo_root / "infra/docker/docker-compose.yml").read_text(encoding="utf-8")

    finder_token = (
        "SIQ_MARKET_REPORT_FINDER_TOKEN="
        "${SIQ_MARKET_REPORT_FINDER_TOKEN:?SIQ_MARKET_REPORT_FINDER_TOKEN is required}"
    )
    rules_token = (
        "SIQ_MARKET_REPORT_RULES_TOKEN="
        "${SIQ_MARKET_REPORT_RULES_TOKEN:?SIQ_MARKET_REPORT_RULES_TOKEN is required}"
    )

    assert compose.count(finder_token) == 3
    assert compose.count(rules_token) == 2
    assert compose.count("SIQ_DEPLOYMENT_PROFILE=${SIQ_DEPLOYMENT_PROFILE:-local}") >= 7
    assert "report-finder:\n    build:" in compose
    report_finder = compose.split("  report-finder:\n", 1)[1].split("\n  market-report-finder:\n", 1)[0]
    assert "    expose:\n      - \"8000\"" in report_finder
    assert "    ports:" not in report_finder

    for relative in (
        "services/market-report-finder/Dockerfile",
        "services/market-report-rules/Dockerfile",
    ):
        dockerfile = (repo_root / relative).read_text(encoding="utf-8")
        assert "SIQ_DEPLOYMENT_PROFILE=docker" in dockerfile, relative


def test_ci_hadolint_covers_market_service_dockerfiles():
    repo_root = Path(__file__).resolve().parents[3]
    workflow = (repo_root / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "services/market-report-finder/Dockerfile" in workflow
    assert "services/market-report-rules/Dockerfile" in workflow


def test_web_production_image_uses_runtime_nginx_api_proxy():
    repo_root = Path(__file__).resolve().parents[3]
    dockerfile = (repo_root / "apps/web/Dockerfile").read_text(encoding="utf-8")
    dockerignore = (repo_root / "apps/web/.dockerignore").read_text(encoding="utf-8")
    nginx_template = (repo_root / "apps/web/nginx.conf.template").read_text(encoding="utf-8")
    entrypoint = (repo_root / "apps/web/docker-entrypoint.sh").read_text(encoding="utf-8")
    compose = (repo_root / "infra/docker/docker-compose.yml").read_text(encoding="utf-8")

    assert "FROM nginxinc/nginx-unprivileged:1.27-alpine" in dockerfile
    assert "COPY nginx.conf.template /etc/nginx/templates/siq.conf.template" in dockerfile
    assert "COPY docker-entrypoint.sh /usr/local/bin/siq-web-entrypoint.sh" in dockerfile
    assert 'ENTRYPOINT ["sh", "/usr/local/bin/siq-web-entrypoint.sh"]' in dockerfile
    assert 'CMD ["nginx", "-c", "/tmp/nginx.conf", "-g", "daemon off;"]' in dockerfile
    assert "USER 101" in dockerfile
    assert "SIQ_BACKEND_URL=http://api:18081" in dockerfile
    assert "npm install -g serve" not in dockerfile
    assert "serve@14" not in dockerfile
    assert 'CMD ["serve"' not in dockerfile
    assert "node_modules" in dockerignore
    assert "dist" in dockerignore

    assert "pid /tmp/nginx.pid;" in nginx_template
    assert "client_body_temp_path /tmp/nginx-client-body;" in nginx_template
    assert "location = /api/health" in nginx_template
    assert "rewrite ^/api/health$ /health break;" in nginx_template
    assert "proxy_pass ${SIQ_BACKEND_URL};" in nginx_template
    assert (
        "location ~ ^/api/(auth|eval|v1|chat|wiki|analysis|factchecker|tracking|legal|settings|system|"
        "market-report-health|market-reports|us-sec|jobs|downloads|workflow|workspace|documents|deals|"
        "primary-market|pdf|pdf_page|source)(/|$)"
    ) in nginx_template
    assert "proxy_pass ${SIQ_REPORT_FINDER_URL};" in nginx_template
    assert "proxy_pass ${SIQ_PDFAPI_URL};" not in nginx_template
    assert "proxy_set_header Host $http_host;" in nginx_template
    assert "proxy_set_header Host $host;" not in nginx_template
    assert "map $http_x_forwarded_proto $siq_forwarded_proto" in nginx_template
    assert nginx_template.count("proxy_set_header X-Forwarded-Proto $siq_forwarded_proto;") == 4
    assert "proxy_set_header X-Forwarded-Proto $scheme;" not in nginx_template
    assert 'proxy_set_header X-PDF2MD-Token "${PDF2MD_ACCESS_TOKEN}";' not in nginx_template
    assert "location = /pdfapi" in nginx_template
    assert "location ^~ /pdfapi/" in nginx_template
    assert nginx_template.count("return 404;") >= 2
    assert 'try_files $uri $uri/ /index.html;' in nginx_template

    assert ": \"${SIQ_BACKEND_URL:=http://api:18081}\"" in entrypoint
    assert ": \"${SIQ_REPORT_FINDER_URL:=http://report-finder:8000}\"" in entrypoint
    assert "SIQ_PDFAPI_URL" not in entrypoint
    assert "PDF2MD_ACCESS_TOKEN" not in entrypoint
    assert "envsubst '${SIQ_BACKEND_URL} ${SIQ_REPORT_FINDER_URL}'" in entrypoint

    assert "wget -q -O /dev/null http://127.0.0.1:15173/api/health" in compose
    assert "node -e \"fetch('http://127.0.0.1:15173/')" not in compose


def test_web_nginx_enforces_upload_batch_limit_with_json_413():
    repo_root = Path(__file__).resolve().parents[3]
    nginx_template = (repo_root / "apps/web/nginx.conf.template").read_text(encoding="utf-8")

    assert "client_max_body_size 210m;" in nginx_template
    assert "error_page 413 = @upload_too_large;" in nginx_template
    assert "location @upload_too_large" in nginx_template
    assert "default_type application/json;" in nginx_template
    assert 'return 413 \'{"detail":"Upload request exceeds the 200 MiB batch limit"}\';' in nginx_template


def test_api_compose_passes_production_cors_guard_into_container():
    repo_root = Path(__file__).resolve().parents[3]
    compose = (repo_root / "infra/docker/docker-compose.yml").read_text(encoding="utf-8")

    assert "SIQ_DEPLOYMENT_PROFILE=${SIQ_DEPLOYMENT_PROFILE:-local}" in compose
    assert "SIQ_CORS_ALLOW_ORIGINS=${SIQ_CORS_ALLOW_ORIGINS:?SIQ_CORS_ALLOW_ORIGINS is required}" in compose


def test_api_metrics_token_is_documented_and_passed_to_container():
    repo_root = Path(__file__).resolve().parents[3]
    compose = (repo_root / "infra/docker/docker-compose.yml").read_text(encoding="utf-8")

    assert "SIQ_METRICS_TOKEN=${SIQ_METRICS_TOKEN:-}" in compose
    assert "SIQ_METRICS_TOKEN=replace-with-secret-manager-value" in (
        repo_root / "infra/env/production.example"
    ).read_text(encoding="utf-8")
    for relative in ("infra/env/local.example", "infra/env/docker.example"):
        assert "SIQ_METRICS_TOKEN=" in (repo_root / relative).read_text(encoding="utf-8")


def test_api_mounts_host_hermes_sessions_read_only_for_trusted_receipts():
    repo_root = Path(__file__).resolve().parents[3]
    compose = (repo_root / "infra/docker/docker-compose.yml").read_text(encoding="utf-8")

    assert "SIQ_HERMES_HOME=/state/var/hermes/home" in compose
    assert "SIQ_HERMES_PROFILES_ROOT=/state/var/hermes/home/profiles" in compose
    assert (
        "SIQ_HERMES_HOST_HOME=${SIQ_HERMES_HOST_HOME:?SIQ_HERMES_HOST_HOME is required}"
        in compose
    )
    assert (
        "${SIQ_HERMES_HOST_HOME:?SIQ_HERMES_HOST_HOME is required}:"
        "/state/var/hermes/home:ro"
        in compose
    )
    for relative in ("infra/env/local.example", "infra/env/docker.example", "infra/env/production.example"):
        assert "SIQ_HERMES_HOST_HOME=" in (repo_root / relative).read_text(encoding="utf-8")


def test_pdf_parser_image_keeps_monorepo_profile_dependencies():
    repo_root = Path(__file__).resolve().parents[3]
    compose = (repo_root / "infra/docker/docker-compose.yml").read_text(encoding="utf-8")
    dockerfile = (repo_root / "apps/pdf-parser/Dockerfile").read_text(encoding="utf-8")

    assert "pdf-parser:\n    build:\n      context: ../..\n      dockerfile: apps/pdf-parser/Dockerfile" in compose
    assert "WORKDIR /app/apps/pdf-parser" in dockerfile
    for source in (
        "packages/market-contracts/",
        "services/market-report-rules/src/",
        "scripts/hk/",
        "scripts/jp/",
        "scripts/kr/",
    ):
        assert f"COPY --chown=siq:siq {source}" in dockerfile
    assert "RUN pip install --no-cache-dir /app/packages/market-contracts" in dockerfile

    requirements = (repo_root / "apps/pdf-parser/requirements.txt").read_text(encoding="utf-8")
    assert "pydantic>=2.8,<3" in requirements


def test_parser_images_use_single_worker_production_wsgi_servers():
    repo_root = Path(__file__).resolve().parents[3]
    parser_contracts = (
        ("apps/pdf-parser", "0.0.0.0:15000", "initialize_app(start_worker=True)"),
        ("apps/document-parser", "0.0.0.0:15010", "atexit.register(stop_worker)"),
    )

    for relative, bind, lifecycle_marker in parser_contracts:
        root = repo_root / relative
        dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
        requirements = (root / "requirements.txt").read_text(encoding="utf-8")
        wsgi = (root / "wsgi.py").read_text(encoding="utf-8")

        assert "gunicorn" in requirements.lower(), relative
        assert '["gunicorn"' in dockerfile, relative
        assert f'"--bind", "{bind}"' in dockerfile, relative
        assert '"--workers", "1"' in dockerfile, relative
        assert '"--graceful-timeout", "30"' in dockerfile, relative
        assert '"--worker-tmp-dir", "/tmp"' in dockerfile, relative
        assert 'CMD ["python", "app.py"]' not in dockerfile, relative
        assert lifecycle_marker in wsgi, relative


def test_compose_default_network_is_project_scoped():
    repo_root = Path(__file__).resolve().parents[3]
    compose = (repo_root / "infra/docker/docker-compose.yml").read_text(encoding="utf-8")

    assert "name: siq_network" not in compose


def test_compose_config_uses_project_scoped_network():
    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "siq-config-contract",
            "--file",
            "infra/docker/docker-compose.yml",
            "--env-file",
            "infra/env/local.example",
            "config",
            "--format",
            "json",
        ],
        cwd=repo_root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    payload = json.loads(result.stdout)
    assert payload["networks"]["default"]["name"] == "siq-config-contract_default"


def test_docker_env_template_renders_main_compose_with_safe_cors():
    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "siq-docker-env-contract",
            "--file",
            "infra/docker/docker-compose.yml",
            "--env-file",
            "infra/env/docker.example",
            "config",
            "--format",
            "json",
        ],
        cwd=repo_root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )

    payload = json.loads(result.stdout)
    api_environment = payload["services"]["api"]["environment"]
    assert api_environment["SIQ_CORS_ALLOW_ORIGINS"] == "http://localhost:15173,http://127.0.0.1:15173"
    assert api_environment["SIQ_REPORT_REVIEW_ROOT"] == "/state/artifacts"
    assert api_environment["SIQ_REPORT_REVIEW_MAX_BYTES"] == "10485760"
    assert api_environment["SIQ_UPLOAD_PROXY_MAX_CONCURRENCY"] == "8"
    assert api_environment["SIQ_UPLOAD_PROXY_QUEUE_TIMEOUT_SECONDS"] == "5"
    assert api_environment["SIQ_IC_TASK_LEASE_SECONDS"] == "120"
    assert api_environment["SIQ_IC_TASK_HEARTBEAT_SECONDS"] == "30"
    assert set(payload["services"]["web"]["depends_on"]) == {"api", "report-finder"}


def test_milvus_compose_uses_loopback_ports_and_non_default_minio_credentials():
    repo_root = Path(__file__).resolve().parents[3]
    compose = (repo_root / "infra/vector-index/milvus/docker-compose.yml").read_text(encoding="utf-8")

    assert "minioadmin" not in compose
    assert "MINIO_ROOT_USER" in compose
    assert "MINIO_ROOT_PASSWORD" in compose
    assert "SIQ_MILVUS_MINIO_ROOT_USER:?" in compose
    assert "SIQ_MILVUS_MINIO_ROOT_PASSWORD:?" in compose
    for public_binding in (
        '"9001:9001"',
        '"9000:9000"',
        '"19530:19530"',
        '"9091:9091"',
        '"8001:8000"',
    ):
        assert public_binding not in compose
    for loopback_binding in (
        '"127.0.0.1:${SIQ_MILVUS_MINIO_CONSOLE_PORT:-9001}:9001"',
        '"127.0.0.1:${SIQ_MILVUS_MINIO_API_PORT:-9000}:9000"',
        '"127.0.0.1:${SIQ_MILVUS_PORT:-19530}:19530"',
        '"127.0.0.1:${SIQ_MILVUS_HEALTH_PORT:-9091}:9091"',
        '"127.0.0.1:${SIQ_MILVUS_ATTU_PORT:-8001}:8000"',
    ):
        assert loopback_binding in compose
