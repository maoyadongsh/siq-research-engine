import json

from scripts.audit_async_sync_session import (
    advisory_buckets,
    finding_summary,
    iter_sync_session_findings,
    main,
    sync_session_usage,
)


EXPECTED_SUMMARY = {
    "total": 0,
    "by_kind": {},
    "by_path": {},
}

EXPECTED_BUCKETS = []


ALLOWED_SYNC_SESSION_USAGE = set()


def test_async_routes_do_not_add_new_sync_session_usage():
    findings = sync_session_usage()
    unexpected = findings - ALLOWED_SYNC_SESSION_USAGE
    stale = ALLOWED_SYNC_SESSION_USAGE - findings

    assert not unexpected, "Unexpected sync Session usage in async routes:\n" + "\n".join(
        sorted(unexpected)
    )
    assert not stale, "Stale sync Session allowlist entries:\n" + "\n".join(sorted(stale))


def test_async_sync_session_audit_summary_and_buckets_are_stable():
    findings = iter_sync_session_findings()

    assert finding_summary(findings) == EXPECTED_SUMMARY
    assert [
        {
            "priority": bucket["priority"],
            "path": bucket["path"],
            "total": bucket["total"],
            "depends_get_session": bucket["depends_get_session"],
            "next_get_session": bucket["next_get_session"],
        }
        for bucket in advisory_buckets(findings)
    ] == EXPECTED_BUCKETS


def test_async_sync_session_audit_scans_nested_async_functions(tmp_path):
    routers_dir = tmp_path / "routers"
    services_dir = tmp_path / "services"
    routers_dir.mkdir()
    services_dir.mkdir()
    (services_dir / "auth_dependencies.py").write_text("async def clean():\n    return None\n", encoding="utf-8")
    (routers_dir / "demo.py").write_text(
        """
from fastapi import Depends
from sqlmodel import Session
from database import get_session


def create_router():
    async def endpoint(session: Session = Depends(get_session)):
        async def done_payload():
            return next(get_session())
        return session
""",
        encoding="utf-8",
    )

    findings = iter_sync_session_findings(tmp_path)

    assert [finding.key for finding in findings] == [
        "routers/demo.py::create_router.endpoint::param session: Session = Depends(get_session)",
        "routers/demo.py::create_router.endpoint.done_payload::body next(get_session())",
    ]
    assert finding_summary(findings) == {
        "total": 2,
        "by_kind": {"depends_get_session": 1, "next_get_session": 1},
        "by_path": {"routers/demo.py": 2},
    }


def test_async_sync_session_audit_json_summary_omits_findings_when_requested(capsys):
    assert main(["--json", "--summary"]) == 0

    payload = json.loads(capsys.readouterr().out)

    assert payload["summary"] == EXPECTED_SUMMARY
    assert "findings" not in payload
    assert payload["advisory"]["buckets"] == EXPECTED_BUCKETS
