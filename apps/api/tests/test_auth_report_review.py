import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import Session, SQLModel, create_engine, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers import auth
from services.auth_service import AuditLog, ReportReview, ReportReviewCreate, ReportSignature, User, UserRole


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'auth-report-review.db'}")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _add_user(session: Session) -> User:
    user = User(
        username="reviewer",
        email="reviewer@example.test",
        full_name="Reviewer",
        hashed_password="x",
        role=UserRole.ANALYST,
        is_active=True,
        approval_status="approved",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _review_payload(report_path: Path) -> ReportReviewCreate:
    return ReportReviewCreate(
        report_path=str(report_path),
        company_id="300017",
        report_year=2025,
        report_type="analysis",
        status="approved",
        review_result={"notes": "ok"},
    )


@pytest.fixture
def report_root(tmp_path, monkeypatch):
    root = tmp_path / "artifacts"
    root.mkdir()
    monkeypatch.setenv("SIQ_REPORT_REVIEW_ROOT", str(root))
    monkeypatch.setenv("SIQ_REPORT_REVIEW_MAX_BYTES", "1048576")
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "test-report-signature-secret-key-32-bytes")
    return root


def test_create_report_review_uses_sibling_report_metadata(tmp_path, report_root):
    report_path = report_root / "companies" / "300017-analysis.md"
    report_path.parent.mkdir()
    report_path.write_text("# Report\n\ncontent", encoding="utf-8")
    report_path.with_suffix(".json").write_text(
        """
        {
          "report_meta": {
            "generator": "run_analysis_report.py",
            "generated_at": "2026-07-03T01:02:03"
          }
        }
        """,
        encoding="utf-8",
    )

    with _session(tmp_path) as session:
        user = _add_user(session)
        result = auth.create_report_review(
            _review_payload(report_path),
            request=SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")),
            current_user=user,
            session=session,
        )
        review = session.exec(select(ReportReview)).one()

    assert result["review_id"] == review.id
    assert review.report_path == "companies/300017-analysis.md"
    assert review.generated_by == "run_analysis_report.py"
    assert review.generated_at == datetime(2026, 7, 3, 1, 2, 3)


def test_create_report_review_falls_back_to_system_without_metadata(tmp_path, report_root):
    report_path = report_root / "300017-analysis.md"
    report_path.write_text("# Report\n\ncontent", encoding="utf-8")

    with _session(tmp_path) as session:
        user = _add_user(session)
        auth.create_report_review(
            _review_payload(report_path),
            request=SimpleNamespace(client=None),
            current_user=user,
            session=session,
        )
        review = session.exec(select(ReportReview)).one()

    assert review.generated_by == "system"
    assert isinstance(review.generated_at, datetime)


def test_create_report_review_ignores_malformed_sibling_metadata(tmp_path, report_root):
    report_path = report_root / "300017-analysis.md"
    report_path.write_text("# Report\n\ncontent", encoding="utf-8")
    report_path.with_suffix(".json").write_text("{not json", encoding="utf-8")

    with _session(tmp_path) as session:
        user = _add_user(session)
        auth.create_report_review(
            _review_payload(report_path),
            request=SimpleNamespace(client=None),
            current_user=user,
            session=session,
        )
        review = session.exec(select(ReportReview)).one()

    assert review.generated_by == "system"
    assert isinstance(review.generated_at, datetime)


def test_create_report_review_accepts_relative_artifact_identity_and_audits_it(tmp_path, report_root):
    report_path = report_root / "companies" / "300017" / "analysis.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("# Report\n\ncontent", encoding="utf-8")
    payload = _review_payload(Path("companies/300017/analysis.md"))

    with _session(tmp_path) as session:
        user = _add_user(session)
        user_id = user.id
        auth.create_report_review(
            payload,
            request=SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")),
            current_user=user,
            session=session,
        )
        review = session.exec(select(ReportReview)).one()
        audit_log = session.exec(select(AuditLog).where(AuditLog.action == "REVIEW_REPORT")).one()

    assert review.report_path == "companies/300017/analysis.md"
    assert audit_log.resource_id == "companies/300017/analysis.md"
    assert str(report_root) not in (audit_log.details or "")
    assert ReportSignature.verify_signature(
        "# Report\n\ncontent",
        review.content_hash,
        review.signature,
        user_id=user_id,
    )


@pytest.mark.parametrize(
    ("path_factory", "expected_status"),
    [
        (lambda root, tmp: tmp / "outside.md", 400),
        (lambda root, tmp: Path("companies/../outside.md"), 400),
        (lambda root, tmp: root / "reports", 400),
    ],
)
def test_create_report_review_rejects_untrusted_paths(
    tmp_path,
    report_root,
    path_factory,
    expected_status,
):
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    directory = report_root / "reports"
    directory.mkdir()
    report_path = path_factory(report_root, tmp_path)

    with _session(tmp_path) as session:
        user = _add_user(session)
        with pytest.raises(HTTPException) as exc_info:
            auth.create_report_review(
                _review_payload(report_path),
                request=SimpleNamespace(client=None),
                current_user=user,
                session=session,
            )

    assert exc_info.value.status_code == expected_status


def test_create_report_review_rejects_symlink_escape(tmp_path, report_root):
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    symlink = report_root / "linked.md"
    symlink.symlink_to(outside)

    with _session(tmp_path) as session:
        user = _add_user(session)
        with pytest.raises(HTTPException) as exc_info:
            auth.create_report_review(
                _review_payload(symlink),
                request=SimpleNamespace(client=None),
                current_user=user,
                session=session,
            )

    assert exc_info.value.status_code == 400


def test_create_report_review_rejects_non_utf8_file(tmp_path, report_root):
    report_path = report_root / "binary.md"
    report_path.write_bytes(b"\xff\xfe\xfd")

    with _session(tmp_path) as session:
        user = _add_user(session)
        with pytest.raises(HTTPException) as exc_info:
            auth.create_report_review(
                _review_payload(report_path),
                request=SimpleNamespace(client=None),
                current_user=user,
                session=session,
            )

    assert exc_info.value.status_code == 415


def test_create_report_review_rejects_oversized_file(tmp_path, report_root, monkeypatch):
    monkeypatch.setenv("SIQ_REPORT_REVIEW_MAX_BYTES", "8")
    report_path = report_root / "large.md"
    report_path.write_text("123456789", encoding="utf-8")

    with _session(tmp_path) as session:
        user = _add_user(session)
        with pytest.raises(HTTPException) as exc_info:
            auth.create_report_review(
                _review_payload(report_path),
                request=SimpleNamespace(client=None),
                current_user=user,
                session=session,
            )

    assert exc_info.value.status_code == 413


def test_report_signature_rejects_tampering(monkeypatch):
    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "test-report-signature-secret-key-32-bytes")
    content = "trusted report"
    content_hash = ReportSignature.calculate_hash(content)
    signature = ReportSignature.sign_report(content, user_id=42)

    assert ReportSignature.verify_signature(content, content_hash, signature, user_id=42)
    assert not ReportSignature.verify_signature(content, content_hash)
    assert not ReportSignature.verify_signature("tampered", content_hash, signature, user_id=42)
    assert not ReportSignature.verify_signature(content, content_hash, "0" * 64, user_id=42)
    assert not ReportSignature.verify_signature(content, content_hash, signature, user_id=7)

    monkeypatch.setenv("SIQ_AUTH_SECRET_KEY", "different-report-signature-key-32-bytes")
    assert not ReportSignature.verify_signature(content, content_hash, signature, user_id=42)
