from pathlib import Path


def test_docker_trivy_observe_runs_as_host_user():
    repo_root = Path(__file__).resolve().parents[3]
    script = (repo_root / "scripts/ci/observe_security_artifacts.sh").read_text(encoding="utf-8")

    assert '--user "$(id -u):$(id -g)"' in script
    assert "--env TRIVY_CACHE_DIR=/tmp/trivy-cache" in script
    assert "-v \"$OUT_DIR:/out\"" in script
