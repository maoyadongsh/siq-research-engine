from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlmodel import SQLModel, Session, create_engine, select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from routers import downloads
from services.usage_service import UserArtifact


def make_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'downloads.db'}")
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def write_report(root: Path, relative_path: str) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"%PDF-1.4\nfake report")
    return path


def link_download(session: Session, *, user_id: int, relative_path: str) -> UserArtifact:
    item = UserArtifact(
        user_id=user_id,
        artifact_type="download",
        artifact_key=relative_path,
        title=Path(relative_path).name,
        path=f"/api/downloads/report-file?path={relative_path}",
        source="market_report",
        global_artifact_id=relative_path,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def test_non_owner_cannot_open_downloaded_report(monkeypatch, tmp_path):
    root = tmp_path / "downloads"
    relative_path = "CN/Demo/2025/annual/report.pdf"
    write_report(root, relative_path)
    monkeypatch.setattr(downloads, "DOWNLOADS_ROOT", root)

    with make_session(tmp_path) as session:
        link_download(session, user_id=1, relative_path=relative_path)

        with pytest.raises(HTTPException) as exc:
            downloads.open_downloaded_report(
                relative_path,
                current_user=SimpleNamespace(id=2, role="analyst"),
                session=session,
            )

    assert exc.value.status_code == 403
    assert exc.value.detail == "File not in current user's workspace"


def test_owner_delete_downloaded_report_unlinks_workspace_without_deleting_file(monkeypatch, tmp_path):
    root = tmp_path / "downloads"
    relative_path = "CN/Demo/2025/annual/report.pdf"
    report_path = write_report(root, relative_path)
    monkeypatch.setattr(downloads, "DOWNLOADS_ROOT", root)

    with make_session(tmp_path) as session:
        link_download(session, user_id=1, relative_path=relative_path)

        result = downloads.delete_downloaded_report(
            relative_path,
            current_user=SimpleNamespace(id=1, role="analyst"),
            session=session,
        )

        links = session.exec(select(UserArtifact).where(UserArtifact.artifact_key == relative_path)).all()

    assert result["deleted"] is False
    assert result["unlinked"] is True
    assert result["relativePath"] == relative_path
    assert report_path.is_file()
    assert links == []


def test_admin_delete_downloaded_report_removes_file(monkeypatch, tmp_path):
    root = tmp_path / "downloads"
    relative_path = "US/Demo/2025/annual/report.pdf"
    report_path = write_report(root, relative_path)
    monkeypatch.setattr(downloads, "DOWNLOADS_ROOT", root)

    with make_session(tmp_path) as session:
        result = downloads.delete_downloaded_report(
            relative_path,
            current_user=SimpleNamespace(id=99, role="super_admin"),
            session=session,
        )

    assert result["deleted"] is True
    assert result["relativePath"] == relative_path
    assert not report_path.exists()
