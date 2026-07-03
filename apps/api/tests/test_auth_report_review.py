import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from sqlmodel import Session, SQLModel, create_engine, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routers import auth
from services.auth_service import ReportReview, ReportReviewCreate, User, UserRole


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


def test_create_report_review_uses_sibling_report_metadata(tmp_path):
    report_path = tmp_path / "300017-analysis.md"
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
    assert review.generated_by == "run_analysis_report.py"
    assert review.generated_at == datetime(2026, 7, 3, 1, 2, 3)


def test_create_report_review_falls_back_to_system_without_metadata(tmp_path):
    report_path = tmp_path / "300017-analysis.md"
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


def test_create_report_review_ignores_malformed_sibling_metadata(tmp_path):
    report_path = tmp_path / "300017-analysis.md"
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
